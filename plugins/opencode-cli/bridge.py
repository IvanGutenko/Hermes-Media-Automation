"""Strict OpenCode ACP adapter: no silent default model and no native actions."""
import json
from pathlib import Path
from types import SimpleNamespace

from agent.copilot_acp_client import CopilotACPClient
from agent.acp_openai_bridge import render_tool_bridge_sections, extract_tool_calls_from_text

MODELS = ('opencode/deepseek-v4.1-flash-free','opencode/glm-5.3-flash-free')


class ProviderQuotaError(RuntimeError):
    status_code = 429


def option(result, category):
    return next((o for o in (result or {}).get('configOptions',[]) if o.get('category')==category),{})


class OpenCodeClient(CopilotACPClient):
    def __init__(self, **kwargs):
        super().__init__(acp_cwd=str(Path.home()/'.cache/hermes-opencode/bridge'),**kwargs)

    def _handle_server_message(self, msg, **kwargs):
        if msg.get('method')=='session/update':
            update=(msg.get('params') or {}).get('update') or {}
            if update.get('sessionUpdate') in ('tool_call','tool_call_update'):
                raise RuntimeError('OpenCode attempted its own tool; Hermes must execute actions')
        kwargs['allow_file_requests']=False
        return super()._handle_server_message(msg,**kwargs)

    def _run_prompt(self, prompt_text, *, timeout_seconds, model=None):
        if model not in MODELS:
            raise ValueError('OpenCode model is outside the approved free set')
        with self._session(timeout_seconds,allow_file_requests=False) as (session,request):
            sid=session['sessionId']
            request('session/set_mode',{'sessionId':sid,'modeId':'hermes-bridge'})
            selected=request('session/set_config_option',{'sessionId':sid,'configId':'model','value':model})
            if option(selected,'model').get('currentValue')!=model:
                raise RuntimeError('OpenCode did not confirm the requested free model')
            thought=option(selected,'thought_level')
            levels={o.get('value') for o in thought.get('options',[])}
            effort=next((v for v in ('max','xhigh','high') if v in levels),None)
            if effort is None:
                raise RuntimeError('OpenCode did not expose a high reasoning option')
            selected=request('session/set_config_option',{'sessionId':sid,
                'configId':thought['id'],'value':effort})
            if option(selected,'thought_level').get('currentValue')!=effort:
                raise RuntimeError('OpenCode did not confirm the requested reasoning effort')
            text=[];reasoning=[]
            try:
                result=request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':prompt_text}]},
                               text_parts=text,reasoning_parts=reasoning)
            except RuntimeError as error:
                if any(s in str(error).lower() for s in ('free usage limit reached','quota','rate limit')):
                    raise ProviderQuotaError('OpenCode free usage limit reached') from None
                raise
            if (result or {}).get('stopReason')!='end_turn' or not ''.join(text).strip():
                raise RuntimeError('OpenCode did not complete the requested model turn')
            return ''.join(text),''.join(reasoning)

    def _create_chat_completion(self, *, model=None, messages=None, tools=None, tool_choice=None,
                                timeout=None, stream=False, **kwargs):
        transcript=[]
        for message in messages or []:
            row={k:v for k,v in message.items() if k in ('role','content','tool_calls','tool_call_id','name')}
            content=row.get('content')
            if isinstance(content,list):
                if any(not isinstance(p,dict) or p.get('type')!='text' for p in content):
                    raise ValueError('Use native Gemini for images; this OpenCode bridge is text-only')
                row['content']='\n'.join(p.get('text','') for p in content)
            transcript.append(row)
        prompt='\n\n'.join(['Continue the supplied Hermes conversation, following its system instructions. '
            'Do not perform actions yourself. Emit the exact tool_call contract for Hermes tools.',
            *render_tool_bridge_sections(tools,tool_choice),json.dumps(transcript,ensure_ascii=False)])
        text,reasoning=self._run_prompt(prompt,timeout_seconds=240,model=model)
        calls,content=extract_tool_calls_from_text(text)
        allowed={t.get('function',{}).get('name') for t in tools or []}
        for call in calls:
            if call.function.name not in allowed or not isinstance(json.loads(call.function.arguments),dict):
                raise RuntimeError('OpenCode returned an invalid Hermes tool call')
        # ACP does not expose per-call token usage here; leave it unknown.
        return SimpleNamespace(model=model,usage=None,choices=[SimpleNamespace(
            message=SimpleNamespace(content=content or None,tool_calls=calls,reasoning=reasoning or None,
                reasoning_content=None,reasoning_details=None),finish_reason='tool_calls' if calls else 'stop')])
