"""Provider fallback must distinguish terminal refusals from uncertain submissions."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import video_provider_pool as pool


class ProviderPoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.out=self.root/'output';self.out.mkdir()
        self.data=self.root/'data';self.data.mkdir()
        self.providers=[{'id':'f','provider':'flow','project_url':'https://flow.google.com/u/0/project/test'},
                        {'id':'v','provider':'vids','account':1}]
        self.prompt='An English shot of a real action followed by an early hard cut.'
        for name,value in [('OUTPUT',self.out),('DATA',self.data)]:
            p=patch.object(pool,name,value);p.start();self.addCleanup(p.stop)

    def receipt(self,command,**kwargs):
        provider=Path(next(arg for arg in command if arg.endswith('_automator.py'))).stem.split('_')[0]
        rid=command[command.index('--run-id')+1]
        d={'run_id':rid,'prompt_sha256':hashlib.sha256(self.prompt.encode()).hexdigest(),
           'status':'error','submission_attempted_at':1,'submitted_at':2}
        if provider=='flow':d.update(failure_kind='quota_exhausted',terminal_failure_observed=True)
        else:
            file=self.out/f'vids_{rid}.mp4';file.write_bytes(b'unit-test-transport-content')
            d.update(status='completed',media_id='new-v',baseline_media_ids=[],prompt_verified_in_editor=True,
                     video_file='/data/output/'+file.name,sha256=hashlib.sha256(file.read_bytes()).hexdigest())
        (self.out/f'{provider}_{rid}.json').write_text(json.dumps(d))
        return type('Result',(),{'stderr':'','returncode':0})()

    def test_all_observed_holds_preserve_the_scene_and_retry_when_the_first_provider_can_recover(self):
        now=pool.time.time()
        pool.holds(('f',{'reason':'quota_exhausted','retry_after':now+3600}))
        pool.holds(('v',{'reason':'feature_unavailable','retry_after':now+7200}))
        with patch.object(pool,'run_browser') as run:
            with self.assertRaises(pool.IncludedProvidersUnavailable) as caught:
                pool.generate_video_file(self.prompt,'Title','waiting',providers=self.providers)
        run.assert_not_called()
        self.assertEqual(caught.exception.retry_after,now+3600)
        self.assertIn('f=quota_exhausted',str(caught.exception))
        self.assertIn('v=feature_unavailable',str(caught.exception))
        state=json.loads((self.out/'generation_routes/waiting.json').read_text())
        self.assertEqual(state['status'],'waiting_for_included_provider')
        self.assertEqual(state['attempts'],[])
        self.assertEqual(state['retry_after'],now+3600)

    def test_terminal_credit_refusal_falls_back_and_completed_scene_is_idempotent(self):
        with patch.object(pool.subprocess,'run',side_effect=self.receipt) as call:
            first=pool.generate_video_file(self.prompt,'Title','case',providers=self.providers)
            second=pool.generate_video_file(self.prompt,'Title','case',resume=True,providers=self.providers)
        self.assertEqual(first,second);self.assertEqual(call.call_count,2)
        state=json.loads((self.out/'generation_routes/case.json').read_text())
        self.assertEqual([a['status'] for a in state['attempts']],['unavailable','completed'])

    def test_submitted_timeout_does_not_send_a_second_provider_request(self):
        def uncertain(command,**kwargs):
            rid=command[command.index('--run-id')+1]
            (self.out/f'flow_{rid}.json').write_text(json.dumps({'status':'error','submitted_at':2,'error':'timeout'}))
            return type('Result',(),{'stderr':'','returncode':1})()
        with patch.object(pool.subprocess,'run',side_effect=uncertain) as call:
            with self.assertRaisesRegex(RuntimeError,'uncertain'):
                pool.generate_video_file(self.prompt,'Title','timeout',providers=self.providers)
        self.assertEqual(call.call_count,1)

    def test_explicit_terminal_failure_allows_included_provider_fallback(self):
        def terminal(command,**kwargs):
            result=self.receipt(command,**kwargs)
            if any(arg.endswith('/flow_automator.py') for arg in command):
                rid=command[command.index('--run-id')+1];path=self.out/f'flow_{rid}.json'
                data=json.loads(path.read_text());data['failure_kind']='generation_failed'
                path.write_text(json.dumps(data))
            return result
        with patch.object(pool.subprocess,'run',side_effect=terminal) as call:
            pool.generate_video_file(self.prompt,'Title','terminal',providers=self.providers)
        self.assertEqual(call.call_count,2)
        self.assertEqual(pool.holds()['f']['reason'],'generation_failed')

    def test_missing_prompt_proof_rejects_a_completed_file(self):
        with self.assertRaisesRegex(RuntimeError,'correlated'):
            pool.artifact({'status':'completed'},self.prompt)

    def test_pre_submission_failure_is_distinct_and_retry_gets_a_new_receipt(self):
        ids=[]
        def failure(command,**kwargs):
            rid=command[command.index('--run-id')+1];ids.append(rid)
            (self.out/f'flow_{rid}.json').write_text(json.dumps({'status':'error','error':'metadata unavailable'}))
            return type('Result',(),{'stderr':'','returncode':1})()
        with patch.object(pool.subprocess,'run',side_effect=failure):
            for _ in range(2):
                with self.assertRaises(pool.BeforeSubmissionFailure):pool.generate_video_file(self.prompt,'Title','before',providers=self.providers)
        self.assertEqual(ids,['before_f_1','before_f_2'])

    def retained_request(self,run_id,age_hours):
        def uncertain(command,**kwargs):
            rid=command[command.index('--run-id')+1]
            (self.out/f'flow_{rid}.json').write_text(json.dumps({'status':'error','submitted_at':2,'error':'timeout'}))
            return type('Result',(),{'stderr':'','returncode':1})()
        with patch.object(pool.subprocess,'run',side_effect=uncertain):
            with self.assertRaisesRegex(RuntimeError,'uncertain'):
                pool.generate_video_file(self.prompt,'Title',run_id,providers=self.providers)
        route=self.out/f'generation_routes/{run_id}.json'
        state=json.loads(route.read_text());state['attempts'][0]['started_at']-=age_hours*3600
        route.write_text(json.dumps(state))
        return uncertain

    def test_a_request_unresolved_for_hours_is_still_retained_and_never_resubmitted(self):
        uncertain=self.retained_request('young',RETAINED_HOURS-1)
        with patch.object(pool.subprocess,'run',side_effect=uncertain) as call:
            with self.assertRaisesRegex(RuntimeError,'uncertain'):
                pool.generate_video_file(self.prompt,'Title','young',providers=self.providers)
        self.assertIn('--resume-download',call.call_args.args[0])
        state=json.loads((self.out/'generation_routes/young.json').read_text())
        self.assertEqual([a['status'] for a in state['attempts']],['active'])

    def test_a_lost_request_is_abandoned_after_the_limit_and_the_scene_is_requested_again(self):
        uncertain=self.retained_request('lost',RETAINED_HOURS+1)
        with patch.object(pool.subprocess,'run',side_effect=uncertain) as call:
            with self.assertRaisesRegex(RuntimeError,'uncertain'):
                pool.generate_video_file(self.prompt,'Title','lost',providers=self.providers)
        command=call.call_args.args[0]
        self.assertEqual(command[command.index('--run-id')+1],'lost_f_2')
        self.assertNotIn('--resume-download',command)
        state=json.loads((self.out/'generation_routes/lost.json').read_text())
        self.assertEqual([a['status'] for a in state['attempts']],['abandoned_unresolved','active'])

    def test_a_clip_that_finished_after_the_limit_is_collected_not_abandoned(self):
        self.retained_request('late',RETAINED_HOURS+1)
        def finishes(command,**kwargs):
            return self.receipt(command)
        route=self.out/'generation_routes/late.json'
        first=json.loads(route.read_text())['attempts'][0]['request_id']
        file=self.out/'flow_late.mp4';file.write_bytes(b'late-clip')
        (self.out/f'flow_{first}.json').write_text(json.dumps({
            'run_id':first,'prompt_sha256':hashlib.sha256(self.prompt.encode()).hexdigest(),'status':'completed','submitted_at':2,
            'media_id':'new-late','baseline_media_ids':[],'prompt_verified_in_editor':True,'video_file':'/data/output/'+file.name,
            'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}))
        with patch.object(pool.subprocess,'run',side_effect=finishes) as call:
            result=pool.generate_video_file(self.prompt,'Title','late',providers=self.providers)
        self.assertEqual(result[0],file)
        state=json.loads(route.read_text())
        self.assertEqual([a['status'] for a in state['attempts']],['completed'])
        for c in call.call_args_list:self.assertNotIn('late_f_2',c.args[0])

RETAINED_HOURS=pool.RETAINED_MAX_SECONDS/3600

if __name__=='__main__':unittest.main()
