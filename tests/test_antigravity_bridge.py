import importlib.util
import json
from pathlib import Path
import unittest
import unittest.mock
import tempfile
from unittest.mock import patch

path = Path(__file__).resolve().parents[1]/'plugins/antigravity-cli/bridge.py'
spec = importlib.util.spec_from_file_location('personal_agy_bridge', path)
bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)


class ProtocolTests(unittest.TestCase):
    def envelope(self, **result):
        return '\n'.join(json.dumps(frame) for frame in [
            {'event':'init','init':{'agent':'hermes-bridge','model':bridge.MODELS[0],'tools':[]}},
            {'event':'result','result':{'status':'SUCCESS','response':'answer',**result}}])

    def test_tool_call_history_and_result_identity_survive(self):
        messages=[{'role':'assistant','content':None,'tool_calls':[{'id':'first','type':'function',
            'function':{'name':'read_file','arguments':'{"path":"note.md"}'}}]},
            {'role':'tool','tool_call_id':'first','content':'actual note'}]
        self.assertEqual(json.loads(bridge.render_messages(messages)),messages)

    def test_multimodal_input_is_not_silently_lost(self):
        with self.assertRaises(ValueError):
            bridge.render_messages([{'role':'user','content':[{'type':'image_url','image_url':{}}]}])

    def test_terminal_success_and_usage_are_preserved(self):
        _, result = bridge.decode_output(self.envelope(usage={'input_tokens':17,'thinking_tokens':8}))
        self.assertEqual(result['usage'],{'input_tokens':17,'thinking_tokens':8})

    def test_zero_exit_waiting_and_missing_result_are_not_success(self):
        for text in (self.envelope(status='WAITING'), self.envelope().splitlines()[0]):
            with self.assertRaises(bridge.CLIError):bridge.decode_output(text)

    def test_quota_is_reported_as_quota(self):
        with self.assertRaises(bridge.CLIError) as error:
            bridge.decode_output(self.envelope(status='ERROR',error='RESOURCE_EXHAUSTED quota exceeded'))
        self.assertEqual(error.exception.status_code,429)

    def test_wrong_agent_or_model_are_rejected(self):
        frames=[json.loads(line) for line in self.envelope().splitlines()]
        for field,value in (('agent','default'),('model','flash-lite')):
            altered=json.loads(json.dumps(frames));altered[0]['init'][field]=value
            with self.assertRaises(bridge.CLIError):
                bridge.decode_output('\n'.join(json.dumps(f) for f in altered))

    def test_native_action_steps_are_rejected_even_with_terminal_success(self):
        action={'event':'step_update','step_update':{'step_type':'tool',
                'tool_name':'run_command','state':'DONE'}}
        with self.assertRaises(bridge.CLIError):
            bridge.decode_output(self.envelope()+'\n'+json.dumps(action))

    def run_create(self, replies, homes):
        """create() with fake Hermes helpers; each reply is one CLI process's stdout."""
        import sys,types
        agent=types.ModuleType('agent');acp=types.ModuleType('agent.acp_openai_bridge')
        acp.render_tool_bridge_sections=lambda *a:[]
        acp.extract_tool_calls_from_text=lambda text:([],text)
        acp.completion_to_stream_chunks=lambda c:c
        tools=types.ModuleType('tools');env=types.ModuleType('tools.environments');local=types.ModuleType('tools.environments.local')
        local.hermes_subprocess_env=lambda:{'HOME':'/default'}
        processes=[unittest.mock.Mock(returncode=0,communicate=unittest.mock.Mock(return_value=(r,''))) for r in replies]
        with patch.dict(sys.modules,{'agent':agent,'agent.acp_openai_bridge':acp,'tools':tools,'tools.environments':env,'tools.environments.local':local}), \
             patch.object(bridge,'account_homes',return_value=homes),patch.object(bridge,'prepare_workspace',return_value='/tmp'), \
             patch.object(bridge.subprocess,'Popen',side_effect=processes) as spawn,patch.dict(bridge._SPENT,clear=True):
            client=bridge.AntigravityCLIClient(command='agy')
            try:answer=client.create(model=bridge.MODELS[0],messages=[{'role':'user','content':'hi'}])
            except bridge.CLIError as error:answer=error
            spent=dict(bridge._SPENT)
        return answer,[c.kwargs['env']['HOME'] for c in spawn.call_args_list],spent

    def test_quota_on_the_first_account_is_answered_by_the_second_and_remembered(self):
        quota=self.envelope(status='ERROR',error='Individual quota reached. Resets in 2h0m0s.')
        answer,homes,spent=self.run_create([quota,self.envelope()],[None,'/second'])
        self.assertEqual(answer.choices[0].message.content,'answer')
        self.assertEqual(homes,['/default','/second'])
        self.assertGreater(spent[None],bridge.time.time()+7000)

    def test_other_errors_do_not_move_to_the_other_account_and_all_spent_reports_quota(self):
        answer,homes,_=self.run_create([self.envelope(status='ERROR',error='Antigravity exploded')],[None,'/second'])
        self.assertIsInstance(answer,bridge.CLIError);self.assertEqual(homes,['/default'])
        quota=self.envelope(status='ERROR',error='quota. Resets in 1h0m0s.')
        answer,homes,_=self.run_create([quota,quota],[None,'/second'])
        self.assertEqual(answer.status_code,429);self.assertEqual(homes,['/default','/second'])

    def test_explicit_flash_high_uses_its_supported_maximum(self):
        self.assertEqual(bridge.model_arguments('gemini-3.8-flash-high'),
                         ['--model','gemini-3.8-flash-high','--effort','high'])
        with self.assertRaises(ValueError):bridge.model_arguments('gemini-3.1-flash-lite')

    def test_network_error_starts_recovery_but_quota_never_rotates_vpn(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manager=root/'hermes-workspace/tools/vpn_manager.py'
            manager.parent.mkdir(parents=True);manager.touch()
            with patch.object(bridge.Path,'home',return_value=root), \
                 patch.object(bridge.subprocess,'Popen') as spawn:
                bridge.recover_route('CLI request timed out')
                self.assertEqual(spawn.call_count,1)
                spawn.reset_mock()
                bridge.recover_route('RESOURCE_EXHAUSTED quota; location temporarily unavailable')
                spawn.assert_not_called()


if __name__=='__main__':unittest.main()
