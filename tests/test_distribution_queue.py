import os
import fcntl,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import browser_process
import content_distribution as d
import media_worker


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'output/runs').mkdir(parents=True);(self.root/'data/distribution').mkdir(parents=True)
        self.config=self.root/'data/media_brands.json'
        self.patches=[patch.object(d,'BASE_DIR',self.root),patch.object(d,'DATA',self.root/'data'),patch.object(d,'CONFIG',self.config)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in self.patches:p.stop()
        self.tmp.cleanup()
    def run_dir(self,run_id,brand,age_hours):
        folder=self.root/'output/runs'/run_id;folder.mkdir()
        (folder/'manifest.json').write_text(json.dumps({'run_id':run_id,'brand':brand,'status':'published','youtube_url':'https://youtu.be/x',
            'completed_at':time.time()-age_hours*3600,'video':str(folder/'short.mp4')}))
        (folder/'script.json').write_text(json.dumps({'youtube_meta':{'title':'T '+run_id,'description':'D'}}))


class VideoQueue(Base):
    def conf(self,**pcfg):
        self.config.write_text(json.dumps({'wrongdev':{'publishing':{'instagram':{'enabled':True,'target':'acct','video_enabled':True,**pcfg}}}}))
    def go(self,publish):
        import video_quality
        with patch.object(d,'publish',publish),patch.object(video_quality,'require_artifact',return_value=None):
            d.distribute_videos()
    def test_every_recent_video_is_offered_newest_first_and_old_ones_are_left_alone(self):
        self.conf(video_interval_seconds=0)
        self.run_dir('old','wrongdev',100);self.run_dir('r1','wrongdev',30);self.run_dir('r2','wrongdev',2)
        sent=[]
        def publish(brand,platform,payload):
            sent.append(payload['source_id'])
            d.save(d.DATA/'distribution'/f"{brand}_{platform}_{d.publication_key(brand,{'target':'acct'},payload['source_id'],'video')}.json",{'status':'published','source_id':payload['source_id']})
            return {'status':'published','url':'u'}
        self.go(publish);self.go(publish);self.go(publish)
        self.assertEqual(sent,['video-r2','video-r1'])
    def test_cadence_blocks_new_sends_but_a_pending_receipt_is_still_reconciled(self):
        self.conf(video_interval_seconds=21600)
        self.run_dir('r1','wrongdev',30);self.run_dir('r2','wrongdev',2)
        (d.DATA/'distribution/wrongdev_instagram_video_cursor.json').write_text(json.dumps({'run_id':'x','last_post_at':time.time()-60}))
        publish=Mock(return_value={'status':'scheduled'})
        self.go(publish);publish.assert_not_called()
        path,_=d.receipt_of('wrongdev','instagram',{'target':'acct'},'video-r1')
        path.write_text(json.dumps({'status':'posted_unverified','source_id':'video-r1','share_attempted_at':time.time()-60}))
        self.go(publish)
        self.assertEqual(publish.call_args.args[2]['source_id'],'video-r1')
    def test_stale_uncertain_video_is_abandoned_not_resent_and_the_queue_moves_on(self):
        self.conf(video_interval_seconds=0)
        self.run_dir('r1','wrongdev',30);self.run_dir('r2','wrongdev',20)
        path,_=d.receipt_of('wrongdev','instagram',{'target':'acct'},'video-r2')
        path.write_text(json.dumps({'status':'posted_unverified','source_id':'video-r2','share_attempted_at':time.time()-13*3600}))
        publish=Mock(return_value={'status':'scheduled'})
        self.go(publish)
        self.assertEqual(publish.call_args.args[2]['source_id'],'video-r1')
        self.assertEqual(json.loads(path.read_text())['status'],'abandoned_unverified')
    def test_a_failure_rests_the_account_with_growing_pauses_and_stops_browser_attempts(self):
        self.conf(video_interval_seconds=0);self.run_dir('r1','wrongdev',2)
        publish=Mock(side_effect=RuntimeError('Instagram receipt retained: boom'))
        with self.assertRaises(RuntimeError):self.go(publish)
        self.go(publish)
        self.assertEqual(publish.call_count,1)
        self.assertTrue(d.target_held('instagram','acct','video'))
        self.assertFalse(d.target_held('instagram','acct','post'))
    def test_a_lost_login_rests_every_media_kind_of_the_account(self):
        d.note_failure('instagram','acct','video',RuntimeError('NeedsLogin: Instagram is not signed in'))
        self.assertTrue(d.target_held('instagram','acct','post'))
        d.note_success('instagram','acct','video')
        self.assertFalse(d.target_held('instagram','acct','post'))
    def test_a_busy_browser_is_not_a_failure(self):
        self.conf(video_interval_seconds=0);self.run_dir('r1','wrongdev',2)
        self.go(Mock(side_effect=d.BrowserBusy('busy')))
        self.assertFalse(d.target_held('instagram','acct','video'))
    def test_a_helper_that_found_the_browser_busy_or_dead_is_not_a_failure_of_the_account(self):
        self.conf(video_interval_seconds=0);self.run_dir('r1','wrongdev',2)
        for text in ('BlockingIOError: [Errno 11] Resource temporarily unavailable','TimeoutError: BrowserType.connect_over_cdp: Timeout'):
            self.go(Mock(side_effect=RuntimeError(text)))
            self.assertFalse(d.target_held('instagram','acct','video'),text)
    def test_budget_limits_browser_publications_per_run(self):
        self.config.write_text(json.dumps({b:{'publishing':{p:{'enabled':True,'target':b+p,'video_enabled':True,'video_interval_seconds':0} for p in ('vk','instagram','tiktok','reddit')}} for b in ('wrongdev',)}))
        self.run_dir('r1','wrongdev',2)
        publish=Mock(return_value={'status':'published','url':'u'})
        import video_quality
        with patch.object(d,'publish',publish),patch.object(video_quality,'require_artifact',return_value=None):d.distribute_videos([2])
        self.assertEqual(publish.call_count,2)


class Lease(Base):
    def test_publisher_waits_for_the_browser_and_marks_itself_waiting(self):
        lock=self.root/'output/.flow-generation.lock'
        holder=lock.open('a');fcntl.flock(holder,fcntl.LOCK_EX)
        self.assertFalse(browser_process.wait_for_lease(0.1,self.root,poll=0.05))
        self.assertTrue(browser_process.publisher_waiting(self.root))
        fcntl.flock(holder,fcntl.LOCK_UN)
        self.assertTrue(browser_process.wait_for_lease(0.1,self.root))
        holder.close()
    def test_an_unresponsive_browser_is_not_blamed_on_the_account(self):
        stuck=Mock(returncode=1,stderr='playwright TimeoutError: BrowserType.connect_over_cdp: Timeout 20000ms exceeded.')
        with patch.object(d,'run_browser',return_value=stuck),patch.object(d,'wait_for_lease',return_value=True):
            with self.assertRaises(browser_process.BrowserBusy):d.run_publisher(['x'],timeout=60)
    def test_a_lost_race_inside_the_container_retries_instead_of_failing(self):
        answers=[Mock(returncode=1,stderr='BlockingIOError: [Errno 11]'),Mock(returncode=0,stderr='')]
        with patch.object(d,'run_browser',side_effect=answers) as run,patch.object(d.time,'sleep'):
            self.assertEqual(d.run_publisher(['x'],timeout=100).returncode,0)
        self.assertEqual(run.call_count,2)
    def test_a_real_publisher_error_is_returned_unchanged(self):
        with patch.object(d,'run_browser',return_value=Mock(returncode=1,stderr='ValueError: bad')):
            self.assertEqual(d.run_publisher(['x'],timeout=100).returncode,1)


class CheapTick(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'data').mkdir()
    def tearDown(self):self.tmp.cleanup()
    def write(self,slot):
        (self.root/'data/media_brands.json').write_text(json.dumps({'migratoria':{'youtube_channel_id':'c','video_enabled':True}}))
        (self.root/'data/media_schedule.json').write_text(json.dumps({'migratoria':slot}))
    def test_nothing_due_only_when_every_brand_is_really_waiting(self):
        self.write({'next_due':10000});self.assertTrue(media_worker.nothing_due(self.root,now=1000))
        self.write({'next_due':10000,'job':'j','retry_after':2000});self.assertTrue(media_worker.nothing_due(self.root,now=1000))
        self.write({'next_due':10000,'job':'j','retry_after':500});self.assertFalse(media_worker.nothing_due(self.root,now=1000))
        self.write({'next_due':2000});self.assertFalse(media_worker.nothing_due(self.root,now=1000))
    def test_any_doubt_runs_the_full_tick(self):
        self.assertFalse(media_worker.nothing_due(self.root,now=1000))


class Agy(unittest.TestCase):
    def test_quota_reset_named_by_the_cli_becomes_the_cooldown(self):
        import text_llm,subprocess
        answer=Mock(returncode=1,stdout=json.dumps({'status':'ERROR','error':'Individual quota reached. Resets in 44h4m14s.'}))
        with patch.object(text_llm.os,'access',return_value=True),patch.object(text_llm.subprocess,'run',return_value=answer):
            with self.assertRaises(text_llm.Unavailable) as caught:
                text_llm._agy('m',[{'role':'user','content':'x'}],max_tokens=1,temperature=0,limit_file=None)
        self.assertEqual(caught.exception.retry_after,44*3600+4*60+14)


class LeaseOwnedByContainer(unittest.TestCase):
    """Reproduces production: the lock belongs to root inside the container and the publisher runs on the host."""
    def test_a_lock_file_this_user_cannot_write_can_still_be_watched(self):
        if os.geteuid()==0:self.skipTest('root ignores file modes')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'output').mkdir()
            lock=browser_process.lease_file(root);lock.touch();lock.chmod(0o444)
            self.assertTrue(browser_process.wait_for_lease(0.1,root))
            with lock.open() as held:
                fcntl.flock(held,fcntl.LOCK_EX)
                self.assertFalse(browser_process.wait_for_lease(0.1,root,poll=0.05))
    def test_an_unreadable_lock_does_not_stop_publishing(self):
        if os.geteuid()==0:self.skipTest('root ignores file modes')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'output').mkdir()
            lock=browser_process.lease_file(root);lock.touch();lock.chmod(0)
            self.assertTrue(browser_process.wait_for_lease(0.1,root))


