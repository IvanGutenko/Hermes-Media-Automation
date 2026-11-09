import fcntl,json,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import media_health as health


class BrowserRecoveryTests(unittest.TestCase):
    busy=None   # the machine running the tests must not decide whether a restart happens

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

    def test_lvl_video_cadence_is_checked_independently_of_its_news_worker_flag(self):
        brands={'lvl':{'lvl_enabled':True,'video_enabled':False,'publish_interval_seconds':7200,
            'languages':['ru','en'],'publishing':{'youtube':{'enabled':True}}}}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'output/lvl_runs/episode';folder.mkdir(parents=True)
            (folder/'manifest.json').write_text(json.dumps({'published':{'youtube:ru':{'at':time.time()-10800}}}))
            report,_,_=self.run_health(root,ready=True,brands=brands)
        overdue=[p for p in report['problems'] if p.get('issue')=='publication_overdue']
        self.assertEqual([(p['brand'],p['platform']) for p in overdue],[('lvl','youtube')])
        self.assertGreaterEqual(overdue[0]['seconds'],3600)

    def test_a_nearly_full_disk_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(health.shutil,'disk_usage',return_value=Mock(free=3*2**30)):
            report,_,_=self.run_health(Path(tmp),ready=True)
        self.assertIn({'issue':'disk_low','free_gb':3.0},report['problems'])

    def test_attachment_timeout_is_an_unready_browser(self):
        with patch('browser_process.run',side_effect=subprocess.TimeoutExpired('probe',60)):
            self.assertFalse(health.browser_ready())

    def run_health(self,root,ready=False,restart_error=False,brands=None,order=None):
        data=root/'data';data.mkdir(exist_ok=True)
        output=root/'output';output.mkdir(exist_ok=True)
        (output/'.flow-generation.lock').touch()
        for name,value in [('media_brands',brands or {}),('media_schedule',{}),('content_feedback',{'errors':{}})]:
            (data/(name+'.json')).write_text(json.dumps(value))
        calls=[]
        def run(command,**kwargs):
            calls.append(command)
            if command[:2]==['docker','restart'] and restart_error:
                raise subprocess.TimeoutExpired(command,45)
            return Mock(returncode=0,stdout='active\n')
        def readiness():
            if order is not None:order.append('probe')
            return ready
        with patch.object(health,'BASE_DIR',root),patch.object(health,'host_busy',return_value=self.busy),patch.object(health,'browser_ready',side_effect=readiness) as probe,patch.object(health.subprocess,'run',side_effect=run),patch('media_network.check',return_value={'status':'reachable'}):
            health.tick()
        return json.loads((data/'media_health.json').read_text()),calls,probe.call_count

    def test_retired_reddit_moderation_is_not_an_active_fault_but_other_platforms_still_are(self):
        brands={'wrongdev':{'publishing':{p:{'enabled':False,'moderation_hold':{'reason':'old rejection'}} for p in ('reddit','vk')}}}
        with tempfile.TemporaryDirectory() as tmp:
            report,_,_=self.run_health(Path(tmp),ready=True,brands=brands)
        self.assertEqual([p['platform'] for p in report['problems'] if p['issue']=='platform_moderation_hold'],['vk'])

    def test_retired_reddit_account_holds_are_not_retried_or_reported_as_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'data/distribution';folder.mkdir(parents=True)
            (folder/'target_holds.json').write_text(json.dumps({p+':account:video':{'fails':3,'until':time.time()+3600} for p in ('reddit','instagram')}))
            report,_,_=self.run_health(root,ready=True)
        self.assertEqual([p['target'] for p in report['problems'] if p['issue']=='platform_account_failing'],['instagram:account:video'])

    def test_repeated_attachment_failure_recovers_and_persists_the_real_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            (root/'data/browser_readiness.json').write_text(json.dumps({'consecutive_failures':1}))
            report,calls,_=self.run_health(root)
            self.assertIn(['docker','restart','video_factory_auth'],calls)
            self.assertEqual(report['status'],'degraded')
            self.assertFalse(json.loads((root/'data/browser_readiness.json').read_text())['ready'])

    def test_a_stalled_host_delays_the_restart_until_the_browser_has_failed_six_times(self):
        self.busy='disk I/O pressure 90.0% over the last minute'
        for previous,restarts in ((1,False),(4,False),(5,True)):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'data').mkdir()
                (root/'data/browser_readiness.json').write_text(json.dumps({'consecutive_failures':previous}))
                report,calls,_=self.run_health(root)
                self.assertEqual(['docker','restart','video_factory_auth'] in calls,restarts,previous)
                self.assertTrue(any(p['issue']=='browser_attachment_unavailable' for p in report['problems']))
                self.assertIn('disk I/O',json.loads((root/'data/browser_readiness.json').read_text())['host_busy'])

    def test_restart_timeout_is_reported_instead_of_aborting_the_health_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            (root/'data/browser_readiness.json').write_text(json.dumps({'consecutive_failures':1}))
            report,_,_=self.run_health(root,restart_error=True)
            self.assertTrue(any('timed out' in p.get('recovery','') for p in report['problems']))

    def test_a_saved_upload_delays_a_restart_but_cannot_keep_a_hung_browser_forever(self):
        for previous,restarts in ((1,False),(5,True)):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'data').mkdir();(root/'output/studio_uploads').mkdir(parents=True)
                (root/'output/studio_uploads/job.json').write_text(json.dumps({'id':'abc','status':'uploaded'}))
                (root/'data/browser_readiness.json').write_text(json.dumps({'consecutive_failures':previous}))
                _,calls,_=self.run_health(root)
                self.assertEqual(['docker','restart','video_factory_auth'] in calls,restarts,previous)

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

    def test_cleanup_precedes_readiness_so_orphan_pages_can_be_reclaimed_during_a_stall(self):
        order=[]
        with tempfile.TemporaryDirectory() as tmp,patch.object(health,'park_idle_browser',side_effect=lambda:order.append('cleanup') or 0) as park:
            report,_,_=self.run_health(Path(tmp),ready=False,order=order)
        park.assert_called_once()
        self.assertEqual(order[:2],['cleanup','probe'])
        self.assertEqual(report['browser_parked'],0)
        self.assertTrue(any(p['issue']=='browser_attachment_unavailable' for p in report['problems']))

    def test_parking_never_touches_a_flow_project_page_that_may_hold_a_generation(self):
        from test_browser_load import run_tidy,FLOW_PROJECT,ACCOUNT
        log,count=run_tidy([ACCOUNT,FLOW_PROJECT,'https://www.youtube.com/watch?v=finished'])
        self.assertEqual(log,[('close','https://www.youtube.com/watch?v=finished')])
        self.assertEqual(count,1)

    def test_parking_also_leaves_the_autoplaying_instagram_and_tiktok_feeds(self):
        from test_browser_load import run_tidy,FLOW_PROJECT,ACCOUNT
        social=['https://www.instagram.com/','https://www.tiktok.com/']
        log,count=run_tidy([ACCOUNT,FLOW_PROJECT]+social)
        self.assertEqual(log,[('close',u) for u in social])
        self.assertEqual(count,2)

    def test_a_timed_out_park_is_not_an_error(self):
        with patch('browser_process.run',side_effect=subprocess.TimeoutExpired('park',60)):
            self.assertEqual(health.park_idle_browser(),0)


if __name__=='__main__':unittest.main()
