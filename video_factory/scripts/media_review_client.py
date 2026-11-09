"""Bounded multimodal review failover; a provider outage never counts as a pass."""
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
import requests
from model_policy import API_MODELS,CLI_MODELS,OPENROUTER_MODELS

BASE_DIR=Path(os.getenv('VIDEO_FACTORY_DIR',Path(__file__).resolve().parent.parent))
AGY_BIN=os.getenv('AGY_BIN','/home/gut/.local/bin/agy')
AGY_PROXY=os.getenv('AGY_PROXY','http://127.0.0.1:17892')
AGY_REVIEW_TIMEOUT=int(os.getenv('MEDIA_AGY_REVIEW_TIMEOUT','300'))
AGY_EXTENSIONS={'video/mp4':'mp4','audio/wav':'wav','audio/mpeg':'mp3','image/jpeg':'jpg','image/png':'png','image/webp':'webp'}


def free_visual_review(parts,attempts,timeout):
    model='nvidia/nemotron-3.5-super-vl:free'
    key=os.getenv('OPENROUTER_API_KEY')
    modalities={p['inlineData']['mimeType'].split('/')[0] for p in parts if p.get('inlineData')}
    if not key or model not in OPENROUTER_MODELS or not modalities or not modalities<={'image','video'}:return None
    proxy={'https':'http://127.0.0.1:17890'}
    try:
        catalog=requests.get('https://openrouter.ai/api/v1/models',proxies=proxy,timeout=25)
        catalog.raise_for_status()
        info=next(m for m in catalog.json()['data'] if m['id']==model)
        if (float(info['pricing']['prompt'])!=0 or float(info['pricing']['completion'])!=0
                or not modalities<=set(info['architecture']['input_modalities'])):
            raise RuntimeError('Free visual endpoint pricing or input capabilities changed')
        content=[]
        for part in parts:
            if 'text' in part:content.append({'type':'text','text':part['text']})
            elif 'inlineData' in part:
                media=part['inlineData'];kind='video_url' if media['mimeType'].startswith('video/') else 'image_url'
                content.append({'type':kind,kind:{'url':'data:'+media['mimeType']+';base64,'+media['data']}})
        r=requests.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':'Bearer '+key},
            proxies=proxy,timeout=timeout,json={'model':model,'messages':[{'role':'user','content':content}],
                'temperature':0,'max_tokens':8000,'response_format':{'type':'json_object'},
                'provider':{'allow_fallbacks':False}})
        attempts.append({'model':model,'provider':'openrouter-free','http':r.status_code,'at':time.time()})
        if not r.ok:return None
        payload=r.json();text=payload['choices'][0]['message']['content']
        result=json.loads(text)
        if payload.get('usage',{}).get('cost') not in (None,0,0.0):raise RuntimeError('Unexpected nonzero cost on an exclusively free endpoint')
        return result,model
    except (requests.RequestException,ValueError,KeyError,StopIteration) as exc:
        attempts.append({'model':model,'error':type(exc).__name__,'at':time.time()})
        return None


def agy_media_review(parts,attempts,timeout=None,home=None):
    """Review inline media with the Antigravity subscription when the API key's quota is spent.

    The CLI is text-only by configuration (file reads are denied), so this call alone is given
    read access to a private temporary directory holding exactly the media under review.
    Anything but one well-formed JSON object is a failed attempt, never a pass."""
    if home is None:
        # Quotas belong to the account: a spent one must not fail the review while another can answer.
        from text_llm import agy_homes
        for account in agy_homes().values():
            result=agy_media_review(parts,attempts,timeout,home=account)
            if result:return result
        return None
    model=CLI_MODELS[0];timeout=timeout or AGY_REVIEW_TIMEOUT
    media=[p['inlineData'] for p in parts if p.get('inlineData')]
    if not media or any(m.get('mimeType') not in AGY_EXTENSIONS for m in media) or not os.access(AGY_BIN,os.X_OK):return None
    root=BASE_DIR/'data'/'agy_review'
    try:
        root.mkdir(parents=True,exist_ok=True)
        work=Path(tempfile.mkdtemp(prefix='r_',dir=root))
    except OSError as exc:
        attempts.append({'model':model,'provider':'antigravity-cli','error':type(exc).__name__,'at':time.time()})
        return None
    try:
        work.chmod(0o700);prompt=[];count=0
        for part in parts:
            if 'text' in part:prompt.append(part['text'])
            elif 'inlineData' in part:
                count+=1;name='media_%d.%s'%(count,AGY_EXTENSIONS[part['inlineData']['mimeType']])
                (work/name).write_bytes(base64.b64decode(part['inlineData']['data']))
                prompt.append('[Attached media file: %s]'%name)
        prompt.append('Open and perceive every attached media file yourself; the files are in the current directory. '
                      'Reply with the requested JSON object only, no markdown and no commentary.')
        settings={'permissions':{'allow':['read_file(%s/*)'%work],
                                 'deny':['write_file(*)','command(*)','read_url(*)','execute_url(*)','mcp(*)']}}
        env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':home,'http_proxy':AGY_PROXY,'https_proxy':AGY_PROXY}
        cmd=[AGY_BIN,'-p','\n\n'.join(prompt),'--add-dir',str(work),'--model',model,'--effort','high',
             '--output-format','json','--settings',json.dumps(settings)]
        record={'model':model,'provider':'antigravity-cli','at':time.time()}
        try:
            done=subprocess.run(cmd,capture_output=True,text=True,env=env,cwd=str(work),timeout=timeout)
            outer=json.loads(done.stdout)
            if done.returncode or outer.get('status')!='SUCCESS':
                record['error']=re.sub(r'\s+',' ',str(outer.get('error') or outer.get('status')))[:160]
                attempts.append(record);return None
            text=str(outer.get('response',''))
            fenced=re.search(r'```(?:json)?\s*(.*?)```',text,re.S)
            result=json.loads((fenced.group(1) if fenced else text).strip())
            if not isinstance(result,dict):raise ValueError('not an object')
        except subprocess.TimeoutExpired:
            record['error']='timeout';attempts.append(record);return None
        except (ValueError,TypeError,AttributeError) as exc:
            record['error']='invalid_review_json' if isinstance(exc,ValueError) else type(exc).__name__
            attempts.append(record);return None
        record['status']='SUCCESS';attempts.append(record)
        return result,model
    except (OSError,ValueError) as exc:
        attempts.append({'model':model,'provider':'antigravity-cli','error':type(exc).__name__,'at':time.time()})
        return None
    finally:
        shutil.rmtree(work,ignore_errors=True)


def review(parts, diagnostic, timeout=90):
    key=os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
    if not key:raise RuntimeError('Media review credential is missing')
    attempts=[]
    # The media route is isolated from the interactive Hermes route and already
    # carries the browser's Google requests. Neither route changes VPN selection.
    routes=('http://127.0.0.1:17893','http://127.0.0.1:17890')
    api_region_unavailable=False
    for model in API_MODELS:
        for proxy in routes:
            try:
                r=requests.post('https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',
                    headers={'x-goog-api-key':key},proxies={'https':proxy,'http':proxy},timeout=timeout,
                    json={'contents':[{'role':'user','parts':parts}],
                          'generationConfig':{'temperature':0,'responseMimeType':'application/json'}})
            except requests.RequestException as exc:
                attempts.append({'model':model,'route':proxy.rsplit(':',1)[-1],
                    'error':type(exc).__name__,'at':time.time()})
                continue
            attempts.append({'model':model,'route':proxy.rsplit(':',1)[-1],
                'http':r.status_code,'at':time.time()})
            if r.ok:
                data=r.json()
                text=''.join(p.get('text','') for p in data.get('candidates',[{}])[0].get('content',{}).get('parts',[]) if not p.get('thought'))
                try:result=json.loads(text)
                except (ValueError,TypeError):
                    attempts[-1]['error']='invalid_review_json'
                    break
                diagnostic.write_text(json.dumps({'attempts':attempts,'selected_model':model},indent=2))
                return result,model
            # Model quota/overload affects both routes; don't retry it on another
            # IP. Only a transport failure justifies trying the independent route.
            error={}
            try:
                error=r.json().get('error',{})
                if isinstance(error,dict):attempts[-1]['provider_status']=error.get('status')
            except (ValueError,TypeError):pass
            if (r.status_code==400 and isinstance(error,dict) and error.get('status')=='FAILED_PRECONDITION'
                    and 'location is not supported' in error.get('message','').lower()):
                # This API product is unavailable here. Do not try another IP or
                # model for the same refusal; use the already approved alternative
                # provider below, which still has to produce a real media review.
                attempts[-1]['error']='api_region_unavailable'
                api_region_unavailable=True
                break
            if r.status_code in (400,401,403):
                diagnostic.write_text(json.dumps({'attempts':attempts},indent=2))
                raise RuntimeError('Media review rejected request: HTTP '+str(r.status_code))
            break
        if api_region_unavailable:break
    # Subscription route before the free tier: the API key has its own quota and the CLI does not.
    alternative=agy_media_review(parts,attempts) or free_visual_review(parts,attempts,timeout)
    if alternative:
        diagnostic.write_text(json.dumps({'attempts':attempts,'selected_model':alternative[1]},indent=2))
        return alternative
    diagnostic.write_text(json.dumps({'attempts':attempts},indent=2))
    raise RuntimeError('All approved media review models unavailable; attempts retained')
