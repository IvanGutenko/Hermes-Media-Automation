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

if __name__=='__main__':unittest.main()
