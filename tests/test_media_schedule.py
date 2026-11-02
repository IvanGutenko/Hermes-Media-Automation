import json
import hashlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import media_worker as w

class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.data=self.base/'data';self.data.mkdir()
        self.config=self.data/'brands.json';self.state=self.data/'state.json'
        self.config.write_text(json.dumps({'wrongdev':{'video_enabled':True,'anchor_epoch':0,'youtube_token':'token','youtube_channel_id':'channel'}}))
        self.state.write_text(json.dumps({'wrongdev':{'next_due':10800}}))
        self.patches=[patch.object(w,'get_next_topic',return_value={'id':1}),patch.object(w,'BASE_DIR',self.base),patch.object(w,'DATA',self.data),patch.object(w,'CONFIG',self.config),patch.object(w,'STATE',self.state),patch.object(w.time,'time',return_value=10801),patch.dict(os.environ)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def generate(self,job_id=None,**kw):
        folder=self.base/'output/runs'/job_id;folder.mkdir(parents=True)
        video=folder/'short.mp4';video.write_bytes(b'candidate video artifact')
        digest=hashlib.sha256(video.read_bytes()).hexdigest()
        manifest={'status':'preview_ready','run_id':job_id,'video':str(video),'sha256':digest,
            'script_review':{'supported':True,'allowed_topic':True},
            'scenes':[{'id':1}],'visual_review':{'video_sha256':digest,'publishable':True,
            'score_1_to_5':4,'scenes':[{'id':1,'matches_narration':True,'meaningful_action_or_edit':True}]}}
        (folder/'manifest.json').write_text(json.dumps(manifest))
        (folder/'script.json').write_text(json.dumps({'youtube_meta':{'title':'title','description':'description','tags':[]}}))
        return manifest
    def test_tick_twice_publishes_once_and_spaces_from_actual_publication(self):
        with patch.object(w,'run_pipeline',side_effect=self.generate) as generate,patch.object(w,'upload_video',return_value={'url':'https://youtu.be/id','status':'success'}) as upload:
            w.tick();w.tick()
        self.assertEqual(generate.call_count,1);self.assertEqual(upload.call_count,1)
        self.assertEqual(json.loads(self.state.read_text())['wrongdev']['next_due'],21601)
    def test_generation_failure_does_not_advance_publication(self):
        with patch.object(w,'run_pipeline',side_effect=RuntimeError('generation failed')),patch.object(w,'upload_video') as upload:
            w.tick()
        state=json.loads(self.state.read_text())['wrongdev']
        self.assertEqual(state['next_due'],10800);self.assertIn('job',state);upload.assert_not_called()

    def test_exhausted_narration_holds_source_without_advancing_due_slot(self):
        from bundled_voiceover import NarrationNeedsReview
        with patch.object(w,'run_pipeline',side_effect=NarrationNeedsReview('bounded rewrites exhausted')),patch.object(w,'upload_video') as upload:w.tick()
        state=json.loads(self.state.read_text())['wrongdev']
        self.assertNotIn('job',state);self.assertIn('last_held_job',state)
        self.assertEqual(state['next_due'],10800);upload.assert_not_called()
        self.assertIn('1',json.loads((self.data/'review_held_video_sources.json').read_text())['wrongdev'])

    def test_rejected_script_holds_only_that_source(self):
        from script_review import ScriptNeedsReview
        with patch.object(w,'run_pipeline',side_effect=ScriptNeedsReview('Unsupported claim')),patch.object(w,'upload_video') as upload:w.tick()
        state=json.loads(self.state.read_text())['wrongdev']
        self.assertNotIn('job',state);self.assertEqual(state['next_due'],10800)
        self.assertIn('1',json.loads((self.data/'review_held_video_sources.json').read_text())['wrongdev'])
        upload.assert_not_called()
    def test_rejected_visual_review_holds_only_that_source_instead_of_rerendering_forever(self):
        from video_quality import VisualReviewNeedsReview
        with patch.object(w,'run_pipeline',side_effect=VisualReviewNeedsReview('score 2/5 is below the 3/5 publication bar')),patch.object(w,'upload_video') as upload:w.tick()
        state=json.loads(self.state.read_text())['wrongdev']
        self.assertNotIn('job',state);self.assertIn('last_held_job',state);self.assertEqual(state['next_due'],10800)
        self.assertIn('1',json.loads((self.data/'review_held_video_sources.json').read_text())['wrongdev'])
        upload.assert_not_called()

    def test_a_confirmed_publication_is_reported_to_the_owner_with_the_new_slot(self):
        with patch.object(w,'run_pipeline',side_effect=self.generate),patch.object(w,'upload_video',return_value={'url':'https://youtu.be/id','status':'success'}),patch('owner_notify.published') as report:
            w.tick()
        report.assert_called_once_with('wrongdev','title','https://youtu.be/id',21601)

    def test_a_failing_owner_report_never_undoes_or_fails_a_publication(self):
        with patch.object(w,'run_pipeline',side_effect=self.generate),patch.object(w,'upload_video',return_value={'url':'https://youtu.be/id','status':'success'}),patch('owner_notify.published',side_effect=RuntimeError('telegram down')):
            w.tick()
        state=json.loads(self.state.read_text())['wrongdev']
        self.assertEqual(state['next_due'],21601);self.assertNotIn('job',state);self.assertNotIn('error',state)

    def test_quota_hold_does_not_generate_or_upload(self):
        (self.data/'tts_quota.json').write_text(json.dumps({'retry_at':50000}))
        with patch.object(w,'run_pipeline') as generate,patch.object(w,'upload_video') as upload:w.tick()
        generate.assert_not_called();upload.assert_not_called()
        self.assertEqual(json.loads(self.state.read_text())['wrongdev']['hold'],'tts_daily_quota_exhausted')

    def test_missing_source_does_not_create_a_stuck_job(self):
        with patch.object(w,'get_next_topic',side_effect=RuntimeError('No source')),patch.object(w,'run_pipeline') as generate:
            w.tick()
        state=json.loads(self.state.read_text())['wrongdev']
        self.assertNotIn('job',state);generate.assert_not_called();self.assertEqual(state['next_due'],10800)

    def test_migratoria_publishes_ready_guide_before_selecting_video_source(self):
        self.config.write_text(json.dumps({'migratoria':{'video_enabled':True,'anchor_epoch':0,'youtube_channel_id':'channel'}}))
        self.state.write_text(json.dumps({'migratoria':{'next_due':10800}}))
        from unittest.mock import Mock
        publish_guide=Mock()
        module=types.SimpleNamespace(migration_post=publish_guide)
        with patch.dict(sys.modules,content_distribution=module),patch.object(w,'get_next_topic',side_effect=[RuntimeError('No posted guide'),{'id':10}]),patch.object(w,'run_pipeline',side_effect=RuntimeError('Stop after source selection')):
            w.tick()
        publish_guide.assert_called_once_with(for_video=True)
        self.assertEqual(json.loads(self.state.read_text())['migratoria']['source_post_id'],10)

    def test_a_new_verified_guide_wakes_the_empty_source_wait_without_advancing_due(self):
        self.config.write_text(json.dumps({'migratoria':{'video_enabled':True,'anchor_epoch':0,'youtube_channel_id':'channel'}}))
        self.state.write_text(json.dumps({'migratoria':{'next_due':10800,'retry_after':50000,
            'error':'No fresh verified Migratoria post is ready for video'}}))
        editor=types.SimpleNamespace(next_video_source=lambda:{'id':14})
        with patch.dict(sys.modules,migratoria_editor=editor),patch.object(w,'get_next_topic',return_value={'id':14}),patch.object(w,'run_pipeline',side_effect=RuntimeError('Stop after selecting new source')) as generate:
            w.tick()
        saved=json.loads(self.state.read_text())['migratoria']
        self.assertEqual(saved['source_post_id'],14);self.assertEqual(saved['next_due'],10800)
        generate.assert_called_once()
    def test_removing_provider_hold_allows_retry_without_waiting_for_old_reset(self):
        state={'wrongdev':{'next_due':10800,'hold':'tts_daily_quota_exhausted','retry_after':50000}}
        self.state.write_text(json.dumps(state))
        with patch.object(w,'run_pipeline',side_effect=self.generate) as generate,patch.object(w,'upload_video',return_value={'url':'https://youtu.be/id','status':'success'}):w.tick()
        self.assertEqual(generate.call_count,1)

    def test_prepare_before_due_but_do_not_publish_early(self):
        with patch.object(w.time,'time',return_value=10700),patch.object(w,'run_pipeline',side_effect=self.generate) as generate,patch.object(w,'upload_video') as upload:w.tick()
        self.assertEqual(generate.call_count,1);upload.assert_not_called()
        with patch.object(w,'run_pipeline') as generate,patch.object(w,'upload_video',return_value={'url':'https://youtu.be/id','status':'success'}) as upload:w.tick()
        generate.assert_not_called();self.assertEqual(upload.call_count,1)

    def test_late_publication_does_not_cause_a_nearby_catch_up_upload(self):
        with patch.object(w.time,'time',return_value=21500),patch.object(w,'run_pipeline',side_effect=self.generate),patch.object(w,'upload_video',return_value={'url':'https://youtu.be/id','status':'success'}):w.tick()
        saved=json.loads(self.state.read_text())['wrongdev']
        self.assertEqual(saved['next_due']-saved['last_published_at'],10800)
        with patch.object(w.time,'time',return_value=21601),patch.object(w,'run_pipeline') as generate,patch.object(w,'upload_video') as upload:w.tick()
        generate.assert_not_called();upload.assert_not_called()

    def hold(self,run_id,source,status='held_for_review',brand='wrongdev'):
        folder=self.base/'output/runs'/run_id;folder.mkdir(parents=True)
        (folder/'manifest.json').write_text(json.dumps({'run_id':run_id,'status':status,'brand':brand,'source_post_id':source}))
        holds=self.data/'review_held_video_sources.json'
        data=json.loads(holds.read_text()) if holds.exists() else {}
        data.setdefault(brand,{})[str(source)]={'run_id':run_id,'reason':'VisualReviewNeedsReview: held'}
        holds.write_text(json.dumps(data))

    def finish(self,resume=None,**kw):
        """What resuming a held run does: complete its manifest into a reviewed, publishable one."""
        folder=self.base/'output/runs'/resume
        video=folder/'short.mp4';video.write_bytes(b'rendered video artifact')
        digest=hashlib.sha256(video.read_bytes()).hexdigest()
        manifest=json.loads((folder/'manifest.json').read_text())
        manifest.update(status='preview_ready',video=str(video),sha256=digest,script_review={'supported':True,'allowed_topic':True},
            scenes=[{'id':1}],visual_review={'video_sha256':digest,'publishable':True,'score_1_to_5':3,
            'scenes':[{'id':1,'matches_narration':True,'meaningful_action_or_edit':True}]})
        manifest.pop('error',None)
        (folder/'manifest.json').write_text(json.dumps(manifest))
        (folder/'script.json').write_text(json.dumps({'youtube_meta':{'title':'held title','description':'d','tags':[]}}))
        return manifest

    def test_a_released_held_run_is_resumed_and_published_instead_of_rendering_its_source_again(self):
        self.hold('held_one',7);self.hold('held_two',8)
        (self.data/'released_held_runs.json').write_text(json.dumps({'wrongdev':['held_one']}))
        with patch.object(w,'run_pipeline',side_effect=self.finish) as pipeline,patch.object(w,'upload_video',return_value={'url':'https://youtu.be/held','status':'success'}) as upload:
            w.tick()
        pipeline.assert_called_once_with(resume='held_one')
        self.assertEqual(upload.call_count,1)
        self.assertEqual(json.loads(self.state.read_text())['wrongdev']['last_publication']['url'],'https://youtu.be/held')
        holds=json.loads((self.data/'review_held_video_sources.json').read_text())['wrongdev']
        self.assertNotIn('7',holds);self.assertIn('8',holds)
        self.assertEqual(json.loads((self.data/'released_held_runs.json').read_text()),{'wrongdev':[]})

    def test_released_runs_are_taken_one_per_slot_in_the_given_order(self):
        self.hold('first',1);self.hold('second',2)
        (self.data/'released_held_runs.json').write_text(json.dumps({'wrongdev':['first','second']}))
        with patch.object(w,'run_pipeline',side_effect=self.finish) as pipeline,patch.object(w,'upload_video',return_value={'url':'https://youtu.be/a','status':'success'}):
            w.tick();w.tick()
        self.assertEqual([c.kwargs['resume'] for c in pipeline.call_args_list],['first'])
        self.assertEqual(json.loads((self.data/'released_held_runs.json').read_text()),{'wrongdev':['second']})

    def test_a_released_run_that_is_no_longer_held_is_dropped_and_normal_selection_continues(self):
        self.hold('done_already',7,status='published')
        (self.data/'released_held_runs.json').write_text(json.dumps({'wrongdev':['missing_run','done_already']}))
        with patch.object(w,'run_pipeline',side_effect=self.generate) as pipeline,patch.object(w,'upload_video',return_value={'url':'https://youtu.be/new','status':'success'}):
            w.tick()
        self.assertEqual(pipeline.call_count,1);self.assertEqual(pipeline.call_args.kwargs['post_id'],1)
        self.assertEqual(json.loads((self.data/'released_held_runs.json').read_text()),{'wrongdev':[]})

    def test_a_run_released_for_another_brand_is_not_taken(self):
        self.hold('other_brand_run',7,brand='migratoria')
        (self.data/'released_held_runs.json').write_text(json.dumps({'wrongdev':['other_brand_run']}))
        with patch.object(w,'run_pipeline',side_effect=self.generate) as pipeline,patch.object(w,'upload_video',return_value={'url':'https://youtu.be/new','status':'success'}):
            w.tick()
        self.assertEqual(pipeline.call_args.kwargs['post_id'],1)
        self.assertIn('7',json.loads((self.data/'review_held_video_sources.json').read_text())['migratoria'])

    def test_an_unreadable_release_queue_never_stops_normal_publishing(self):
        (self.data/'released_held_runs.json').write_text('{not json')
        with patch.object(w,'run_pipeline',side_effect=self.generate) as pipeline,patch.object(w,'upload_video',return_value={'url':'https://youtu.be/new','status':'success'}) as upload:
            w.tick()
        self.assertEqual(pipeline.call_count,1);self.assertEqual(upload.call_count,1)

    def test_releasing_a_migratoria_run_also_clears_its_source_exclusion(self):
        self.config.write_text(json.dumps({'migratoria':{'video_enabled':True,'anchor_epoch':0,'youtube_channel_id':'channel'}}))
        self.state.write_text(json.dumps({'migratoria':{'next_due':10800}}))
        self.hold('mig_run',16,brand='migratoria')
        (self.data/'released_held_runs.json').write_text(json.dumps({'migratoria':['mig_run']}))
        statements=[]
        class Connection:
            def __enter__(self):return self
            def __exit__(self,*a):return False
            def execute(self,sql,args=()):statements.append((sql,args))
        editor=types.SimpleNamespace(db=lambda:Connection())
        with patch.dict(sys.modules,migratoria_editor=editor):
            released=w.next_released_run('migratoria')
        self.assertEqual(released,{'run_id':'mig_run','source_post_id':16})
        self.assertEqual(statements[0][1],(16,'%:mig_run'));self.assertIn('video_exclusion_reason=NULL',statements[0][0])

    def test_changed_render_is_not_published_under_an_old_review(self):
        with patch.object(w.time,'time',return_value=10700),patch.object(w,'run_pipeline',side_effect=self.generate):w.tick()
        job=json.loads(self.state.read_text())['wrongdev']['job']
        (self.base/'output/runs'/job/'short.mp4').write_bytes(b'changed after review')
        with patch.object(w,'upload_video') as upload:w.tick()
        upload.assert_not_called()
        self.assertEqual(json.loads(self.state.read_text())['wrongdev']['next_due'],10800)

if __name__=='__main__':unittest.main()
