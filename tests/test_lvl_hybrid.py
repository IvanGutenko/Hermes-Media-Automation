"""Episode identity, fresh footage, real EOF and publication guards for every LVL target."""
import array,hashlib,io,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import MagicMock,Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import lvl_hybrid as h
import lvl_pipeline as p
import lvl_worker as w
import lvl_voice
import video_provider_pool as pool


class HybridPlan(unittest.TestCase):
    def setUp(self):
        self.episode={'title_en':'Reading','scenes':[{'text_ru':'Текст','text_en':'Text','setting':'office'} for _ in range(5)]}
        self.prepared={lang:[{'duration':3} for _ in range(5)] for lang in ('ru','en')}
        self.plan={'scenes':[{'id':1,'video_prompt_en':'A tactile book opens and blank paper sheets fold into a physical spiral on a lilac desk. '*4},
                             {'id':3,'video_prompt_en':'Small blank index cards travel from an open notebook into separated paper storage boxes. '*4}]}

    def test_two_concrete_different_prompts_fit_both_language_timelines(self):
        self.assertIs(h.validate_plan(self.plan,self.episode,self.prepared),self.plan)
        self.prepared['en'][2]['duration']=7.9
        with self.assertRaisesRegex(ValueError,'eight seconds'):h.validate_plan(self.plan,self.episode,self.prepared)

    def test_duplicate_ids_missing_insert_invalid_prompt_and_foreign_scene_are_rejected(self):
        for bad in (None,{'scenes':[]},{'scenes':[self.plan['scenes'][0]]},{'scenes':[self.plan['scenes'][0]]*2},
                    {'scenes':[self.plan['scenes'][0],{'id':9,'video_prompt_en':'x'*300}]},
                    {'scenes':[self.plan['scenes'][0],{'id':3,'video_prompt_en':'Русский промпт '*30}]}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):h.validate_plan(bad,self.episode,self.prepared)

    def test_saved_episode_uses_same_two_request_ids_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)/'episode';run.mkdir();raw=Path(tmp)/'new.mp4';raw.write_bytes(b'own footage')
            receipt={'media_id':'new-card'}
            with patch('lvl_script._llm',side_effect=[(self.plan,'director'),({'ok':True,'issues':[]},'auditor')]) as llm, \
                 patch.object(pool,'generate_video_file',return_value=(raw,receipt)) as generate, \
                 patch('browser_process.wait_for_lease',return_value=True),patch.object(p,'probe',return_value={'format':{'duration':'8'}}):
                first=h.ensure(run,self.episode,self.prepared);second=h.ensure(run,self.episode,self.prepared)
            self.assertEqual(llm.call_count,2)
            requests=[c.args[2] for c in generate.call_args_list]
            self.assertEqual(requests[:2],requests[2:]);self.assertNotEqual(requests[0],requests[1])
            self.assertEqual(first['episode_sha256'],second['episode_sha256'])
            with patch('lvl_script._llm') as llm, self.assertRaisesRegex(RuntimeError,'identity changed'):
                h.ensure(run,{**self.episode,'title_en':'Other'},self.prepared)
            llm.assert_not_called()

    def test_provider_failure_keeps_saved_plan_and_retry_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp);error=pool.IncludedProvidersUnavailable({},time.time()+1000)
            with patch('lvl_script._llm',side_effect=[(self.plan,'director'),({'ok':True,'issues':[]},'auditor')]), \
                 patch.object(pool,'generate_video_file',side_effect=error),patch('browser_process.wait_for_lease',return_value=True):
                with self.assertRaises(h.MediaPending) as ctx:h.ensure(run,self.episode,self.prepared)
            self.assertEqual(ctx.exception.retry_after,error.retry_after)
            self.assertTrue((run/'hybrid/storyboard.json').exists())

    def test_completed_insert_receipts_resume_without_waiting_for_the_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run=root/'episode';run.mkdir();raw=root/'own.mp4';raw.write_bytes(b'own')
            with patch.object(pool,'OUTPUT',root),patch('lvl_script._llm',side_effect=[(self.plan,'director'),({'ok':True,'issues':[]},'auditor')]), \
                 patch.object(pool,'generate_video_file',return_value=(raw,{'media_id':'new'})), \
                 patch('browser_process.wait_for_lease',return_value=True) as wait,patch.object(p,'probe',return_value={'format':{'duration':'8'}}):
                h.ensure(run,self.episode,self.prepared)
                routes=root/'generation_routes';routes.mkdir()
                for s in self.plan['scenes']:(routes/(h.request_id(run,s['id'])+'.json')).write_text('{"status":"completed"}')
                wait.side_effect=AssertionError('cached footage must not take the browser')
                h.ensure(run,self.episode,self.prepared)

    def test_readable_invented_schedule_is_rejected_before_any_generation(self):
        bad=json.loads(json.dumps(self.plan));bad['scenes'][1]['video_prompt_en']+=" Slots marked '1 day' and '3 days'."
        with self.assertRaisesRegex(ValueError,'written labels'):h.validate_plan(bad,self.episode,self.prepared)

    def test_unrequested_bad_plan_is_repaired_without_changing_an_existing_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'hybrid';folder.mkdir();path=folder/'storyboard.json'
            old=json.loads(json.dumps(self.plan));old['scenes'][1]['video_prompt_en']+=" Slots marked '1 day'."
            old.update(system=h.SYSTEM,episode_sha256=h.episode_digest(self.episode));path.write_text(json.dumps(old))
            routes=root/'generation_routes';routes.mkdir();frozen=routes/(h.request_id(root,1)+'.json');frozen.write_text('{}')
            with patch.object(pool,'OUTPUT',root),patch('lvl_script._llm',side_effect=[(self.plan,'director'),({'ok':True,'issues':[]},'auditor')]),patch.object(pool,'generate_video_file') as generate:
                result=h.checked_plan(root,self.episode,self.prepared,path)
            generate.assert_not_called();self.assertEqual(result['scenes'][0],old['scenes'][0])
            self.assertEqual(frozen.read_text(),'{}');self.assertTrue(list(folder.glob('storyboard.before-repair.*.json')))

    def test_a_rejected_storyboard_cannot_spend_video_credits(self):
        def llm(messages,*_):
            return ({'ok':False,'issues':['unrelated footage']},'reviewer') if messages[0]['content'].startswith('Audit ONLY') else (json.loads(json.dumps(self.plan)),'director')
        with tempfile.TemporaryDirectory() as tmp,patch('lvl_script._llm',side_effect=llm),patch.object(pool,'generate_video_file') as generate:
            with self.assertRaisesRegex(ValueError,'Storyboard audit'):h.ensure(Path(tmp),self.episode,self.prepared)
        generate.assert_not_called()

    def test_director_cannot_approve_itself_by_including_an_audit_field(self):
        self.plan['audit']={'ok':True,'plan_sha256':hashlib.sha256(json.dumps(self.plan['scenes'],sort_keys=True).encode()).hexdigest()}
        def llm(messages,*_):
            return ({'ok':False,'issues':['unrelated physical action']},'independent audit') if messages[0]['content'].startswith('Audit ONLY') else (json.loads(json.dumps(self.plan)),'director')
        with tempfile.TemporaryDirectory() as tmp,patch('lvl_script._llm',side_effect=llm),patch.object(pool,'generate_video_file') as generate:
            with self.assertRaisesRegex(ValueError,'Storyboard audit'):h.ensure(Path(tmp),self.episode,self.prepared)
        generate.assert_not_called()


class PublicationGuards(unittest.TestCase):
    def test_social_publisher_cannot_substitute_a_frozen_old_file_for_the_reviewed_master(self):
        import content_distribution as cd
        with tempfile.TemporaryDirectory() as tmp,patch.object(cd,'BASE_DIR',Path(tmp)),patch.object(cd,'run_publisher') as run:
            out=Path(tmp)/'output';out.mkdir();master=out/'new.mp4';master.write_bytes(b'new hybrid master')
            for platform in ('instagram','tiktok'):
                (out/(platform+'_native_job_key.json')).write_text(json.dumps({'sha256':'old hash','video':'old.mp4'}))
                with self.assertRaisesRegex(RuntimeError,'earlier master'):
                    cd.native_social(platform,{'target':'growmodes'},{'video':str(master)}, {'key':'key'},lambda:None)
            run.assert_not_called()

    def test_every_platform_refuses_legacy_cartoon_before_an_upload(self):
        legacy={'run_id':'old','videos':{'ru':'never-read.mp4'}}
        for platform in w.PLATFORMS:
            with self.subTest(platform=platform),patch('content_distribution.publish') as publish, \
                 patch('youtube_uploader.upload_video_studio') as upload,self.assertRaisesRegex(RuntimeError,'hybrid upgrade'):
                w.publish_one(platform,'ru',legacy,{}, {'publishing':{platform:{}}})
            publish.assert_not_called();upload.assert_not_called()

    def fixture(self,root):
        episode={'title_en':'Our script','scenes':[{}, {}, {}]};identity=h.episode_digest(episode)
        assets={};plan={'episode_sha256':identity,'scenes':[]}
        for index in (1,3):
            raw=root/f'fresh{index}.mp4';raw.write_bytes(f'new {index}'.encode());prompt='Original English action '+str(index)
            receipt={'status':'completed','prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                'prompt_verified_in_editor':True,'media_id':str(index),'baseline_media_ids':[],
                'submitted_at':123,'video_file':'/data/output/'+raw.name,'sha256':h.digest(raw),'voiceover_ru':None}
            assets[str(index)]={'raw_video':str(raw),'sha256':h.digest(raw),'media_id':str(index),'episode_sha256':identity,'receipt':receipt}
            plan['scenes'].append({'id':index,'video_prompt_en':prompt})
        plan['audit']={'ok':True,'plan_sha256':hashlib.sha256(json.dumps(plan['scenes'],sort_keys=True).encode()).hexdigest()}
        storyboard=root/'storyboard.json';storyboard.write_text(json.dumps(plan))
        video=root/'master.mp4';video.write_bytes(b'actual reviewed master');sha=h.digest(video)
        review={'video_sha256':sha,'publishable':True,'score_1_to_5':4,'scenes':[{'matches_narration':True,'meaningful_action_or_edit':True} for _ in range(3)]}
        manifest={'motion_system':h.SYSTEM,'visual_mode':'hybrid','scene_count':3,'videos':{'ru':str(video)},'sha256':{'ru':sha},
            'hybrid':{'scenes':assets,'episode_sha256':identity,'storyboard':str(storyboard)},'visual_reviews':{'ru':review}}
        return manifest,episode

    def test_master_and_both_provider_receipts_are_checked_again_at_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);manifest,episode=self.fixture(root)
            with patch.object(pool,'OUTPUT',root):
                h.require_artifact(manifest,'ru',episode)
                (root/'fresh1.mp4').write_bytes(b'different clip')
                with self.assertRaisesRegex(RuntimeError,'insert differs'):h.require_artifact(manifest,'ru',episode)

    def test_altered_script_master_review_or_preexisting_card_cannot_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(pool,'OUTPUT',root):
                manifest,episode=self.fixture(root)
                with self.assertRaisesRegex(RuntimeError,'script changed'):h.require_artifact(manifest,'ru',{**episode,'title_en':'changed'})
                manifest['hybrid']['scenes']['1']['receipt']['baseline_media_ids']=['1']
                with self.assertRaisesRegex(RuntimeError,'new correlated'):h.require_artifact(manifest,'ru',episode)
                manifest,episode=self.fixture(root);manifest['visual_reviews']['ru']['publishable']=False
                with self.assertRaises(RuntimeError):h.require_artifact(manifest,'ru',episode)
                manifest,episode=self.fixture(root);(root/'master.mp4').write_bytes(b'changed master')
                with self.assertRaisesRegex(RuntimeError,'actual visual review'):h.require_artifact(manifest,'ru',episode)

    def test_queued_upgrade_only_selects_languages_with_unpublished_targets(self):
        manifest={'run_id':'episode','status':'ready','published':{'telegram:en':{'url':'old'},'youtube:en':{'url':'old'}}}
        cfg={'post_hour_utc':{'ru':15,'en':0},'publishing':{'telegram':{'enabled':True},'youtube':{'enabled':True}}}
        state={}
        with patch.object(w,'episodes',return_value=[(Path('/unused'),manifest)]),patch.object(w,'save_state'), \
             patch.object(h,'require_artifact',side_effect=RuntimeError('legacy')),patch.object(p,'upgrade',return_value=manifest) as upgrade:
            w.upgrade_pending(cfg,state,lambda *_:None)
        self.assertEqual(upgrade.call_args.args[:2],('episode',['ru']))
        self.assertEqual(manifest['published']['youtube:en']['url'],'old')

    def test_waiting_media_job_survives_attempt_limit_and_age_without_a_new_script(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(p,'RUNS',Path(tmp)):
            run=Path(tmp)/'episode';run.mkdir();(run/'script.json').write_text('{}')
            (run/'manifest.json').write_text(json.dumps({'status':'waiting_media','attempts':99,'started_at':1}))
            self.assertEqual(p.find_resumable()[0],run)

    def test_cached_visual_rejection_holds_only_that_episode_and_preserves_public_receipts(self):
        from video_quality import VisualReviewNeedsReview
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'manifest.json';manifest={'run_id':'episode','status':'ready','published':{'youtube:en':{'url':'existing'}}};path.write_text(json.dumps(manifest))
            cfg={'post_hour_utc':{'ru':15},'publishing':{'youtube':{'enabled':True}}}
            with patch.object(w,'episodes',return_value=[(path,manifest)]),patch.object(w,'save_state'),patch.object(w,'_alert'), \
                 patch.object(h,'require_artifact',side_effect=RuntimeError('legacy')),patch.object(p,'upgrade',side_effect=VisualReviewNeedsReview('actual video rejected')):
                w.upgrade_pending(cfg,{},lambda *_:None)
            held=json.loads(path.read_text());self.assertEqual(held['status'],'held_for_visual_revision');self.assertEqual(held['published'],manifest['published'])


class SceneVoiceCache(unittest.TestCase):
    def test_provider_retry_uses_exact_saved_speech_and_word_times(self):
        episode={'scenes':[{'speaker':'lvl','text_ru':'Свой текст','text_en':'Own text','actors':[{'id':'lvl','side':'center','expression':'happy','action':'cheer'}],
            'setting':'office','props':[],'popup':None,'level_up':False}]}
        cast={'lvl':{'voice':'hero','spec':{}}}
        voice={'samples':array.array('h',[123]*22050),'words':[{'text':'Свой','start':0,'end':.5},{'text':'текст','start':.5,'end':1}],
               'duration':1,'voice':'ru-RU-DmitryNeural'}
        with tempfile.TemporaryDirectory() as tmp,patch.object(lvl_voice,'speak',return_value=voice) as speak:
            cache=Path(tmp);one=p.render_scenes(episode,cast,'ru',cache);two=p.render_scenes(episode,cast,'ru',cache)
            self.assertEqual(speak.call_count,1);self.assertEqual(one,two)
            next(cache.glob('*.wav')).write_bytes(b'changed')
            with self.assertRaisesRegex(p.ProduceError,'saved voice differs'):p.render_scenes(episode,cast,'ru',cache)
            self.assertEqual(speak.call_count,1)


class StreamingFootage(unittest.TestCase):
    def test_short_decoder_output_raises_instead_of_freezing_the_last_frame(self):
        reader=h.Frames([{'raw_video':'own.mp4'}]);reader.width=reader.height=2
        proc=Mock();proc.stdout=io.BytesIO(bytes(range(12)));proc.poll.return_value=0
        with patch.object(h.subprocess,'Popen',return_value=proc),patch.object(lvl_voice,'ffmpeg_cmd',return_value=['ffmpeg']),patch.object(lvl_voice,'runtime_path',return_value='own.mp4'):
            reader.frame(0,0)
            with self.assertRaisesRegex(RuntimeError,'no frozen replacement'):reader.frame(0,1/24)
            reader.close()

    def test_cartoon_only_scene_never_opens_a_video_decoder(self):
        reader=h.Frames([{}])
        with patch.object(h.subprocess,'Popen') as popen:self.assertIsNone(reader.frame(0,2))
        popen.assert_not_called()

if __name__=='__main__':unittest.main()
