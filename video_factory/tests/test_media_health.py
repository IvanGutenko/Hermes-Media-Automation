import fcntl,json,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import media_health as health


class BrowserRecoveryTests(unittest.TestCase):
    def test_instagram_post_that_may_be_public_but_is_not_verified_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir();(root/'output').mkdir()
            old=time.time()-3600
            (root/('output/native_instagram_'+'a'*64+'.json')).write_text(json.dumps({'status':'posted_unverified','source_id':'video-1','share_attempted_at':old}))
            (root/('output/native_instagram_'+'b'*64+'.json')).write_text(json.dumps({'status':'published','source_id':'video-2','share_attempted_at':old}))
            (root/('output/native_instagram_'+'c'*64+'.json')).write_text(json.dumps({'status':'uploading','source_id':'video-3'}))
            report,_,_=self.run_health(root,ready=True)
        issues=[p for p in report['problems'] if p['issue']=='instagram_publication_unverified']
        self.assertEqual([p['source_id'] for p in issues],['video-1'])

    def test_a_nearly_full_disk_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(health.shutil,'disk_usage',return_value=Mock(free=3*2**30)):
            report,_,_=self.run_health(Path(tmp),ready=True)
        self.assertIn({'issue':'disk_low','free_gb':3.0},report['problems'])

    def test_attachment_timeout_is_an_unready_browser(self):
        with patch('browser_process.run',side_effect=subprocess.TimeoutExpired('probe',60)):
            self.assertFalse(health.browser_ready())

    def run_health(self,root,ready=False,restart_error=False):
        data=root/'data';data.mkdir(exist_ok=True)
        output=root/'output';output.mkdir(exist_ok=True)
        (output/'.flow-generation.lock').touch()
        for name,value in [('media_brands',{}),('media_schedule',{}),('content_feedback',{'errors':{}})]:
            (data/(name+'.json')).write_text(json.dumps(value))
        calls=[]
        def run(command,**kwargs):
            calls.append(command)
            if command[:2]==['docker','restart'] and restart_error:
                raise subprocess.TimeoutExpired(command,45)
            return Mock(returncode=0,stdout='active\n')
        with patch.object(health,'BASE_DIR',root),patch.object(health,'browser_ready',return_value=ready) as probe,patch.object(health.subprocess,'run',side_effect=run),patch('media_network.check',return_value={'status':'reachable'}):
            health.tick()
        return json.loads((data/'media_health.json').read_text()),calls,probe.call_count

    def test_repeated_attachment_failure_recovers_and_persists_the_real_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            (root/'data/browser_readiness.json').write_text(json.dumps({'consecutive_failures':1}))
            report,calls,_=self.run_health(root)
            self.assertIn(['docker','restart','video_factory_auth'],calls)
            self.assertEqual(report['status'],'degraded')
            self.assertFalse(json.loads((root/'data/browser_readiness.json').read_text())['ready'])

    def test_restart_timeout_is_reported_instead_of_aborting_the_health_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            (root/'data/browser_readiness.json').write_text(json.dumps({'consecutive_failures':1}))
            report,_,_=self.run_health(root,restart_error=True)
            self.assertTrue(any('timed out' in p.get('recovery','') for p in report['problems']))

    def test_an_active_browser_job_is_never_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'output').mkdir()
            with (root/'output/.flow-generation.lock').open('w') as lease:
                fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
                _,calls,probes=self.run_health(root)
            self.assertEqual(probes,0)
            self.assertNotIn(['docker','restart','video_factory_auth'],calls)

    def test_an_idle_ready_browser_is_parked_off_the_animated_flow_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(health,'park_idle_browser',return_value=1) as park:
                report,_,_=self.run_health(Path(tmp),ready=True)
            park.assert_called_once();self.assertEqual(report['browser_parked'],1)

    def test_a_running_job_keeps_its_flow_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'output').mkdir()
            with (root/'output/.flow-generation.lock').open('w') as lease,patch.object(health,'park_idle_browser') as park:
                fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
                report,_,_=self.run_health(root,ready=True)
            park.assert_not_called();self.assertEqual(report['browser_parked'],0)

    def test_an_unready_browser_is_not_parked(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(health,'park_idle_browser') as park:
            self.run_health(Path(tmp),ready=False)
        park.assert_not_called()

    def test_parking_never_touches_a_flow_project_page_that_may_hold_a_generation(self):
        with patch('browser_process.run',return_value=Mock(returncode=0,stdout='1\n')) as run:
            self.assertEqual(health.park_idle_browser(),1)
        code=run.call_args.args[0][-1]
        self.assertIn("'/project/' not in u.path",code);self.assertIn("u.netloc=='flow.google.com'",code)
        self.assertIn(health.PARK_URL,code)

    def test_parking_also_leaves_the_autoplaying_instagram_and_tiktok_feeds(self):
        with patch('browser_process.run',return_value=Mock(returncode=0,stdout='2\n')) as run:
            self.assertEqual(health.park_idle_browser(),2)
        code=run.call_args.args[0][-1]
        self.assertIn("SOCIAL=('instagram.com','tiktok.com')",code);self.assertIn('u.netloc.endswith(SOCIAL)',code)
        self.assertIn("'/project/' not in u.path",code)

    def test_a_timed_out_park_is_not_an_error(self):
        with patch('browser_process.run',side_effect=subprocess.TimeoutExpired('park',60)):
            self.assertEqual(health.park_idle_browser(),0)


if __name__=='__main__':unittest.main()
