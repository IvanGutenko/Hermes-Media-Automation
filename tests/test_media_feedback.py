import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import media_review_client as client
import content_feedback as feedback
import voice_experiments as voices


class ReviewFailoverTests(unittest.TestCase):
    def test_overload_tries_other_model_without_counting_failure_as_review(self):
        overloaded=Mock(ok=False,status_code=503)
        healthy=Mock(ok=True,status_code=200)
        healthy.json.return_value={'candidates':[{'content':{'parts':[{'text':'{"usable":true}'}]}}]}
        with tempfile.TemporaryDirectory() as tmp,patch.dict(client.os.environ,GEMINI_API_KEY='test'),patch.object(client.requests,'post',side_effect=[overloaded,healthy]) as post:
            result,model=client.review([{'text':'review actual image'}],Path(tmp)/'attempts.json')
            self.assertTrue(result['usable']);self.assertEqual(model,client.API_MODELS[1])
            self.assertEqual(post.call_count,2)
    def test_all_overloaded_remains_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(client.os.environ,GEMINI_API_KEY='test',OPENROUTER_API_KEY=''),patch.object(client.requests,'post',return_value=Mock(ok=False,status_code=503)):
            with self.assertRaisesRegex(RuntimeError,'unavailable'):client.review([],Path(tmp)/'attempts.json')

    def test_free_visual_fallback_refuses_a_nonzero_endpoint_price(self):
        catalog=Mock();catalog.json.return_value={'data':[{'id':'nvidia/nemotron-3.5-super-vl:free','pricing':{'prompt':'0.1','completion':'0'},'architecture':{'input_modalities':['image']}}]}
        with patch.dict(client.os.environ,OPENROUTER_API_KEY='test'),patch.object(client.requests,'get',return_value=catalog),patch.object(client.requests,'post') as post:
            with self.assertRaisesRegex(RuntimeError,'pricing'):client.free_visual_review([{'inlineData':{'mimeType':'image/jpeg','data':'test'}}],[],30)
        post.assert_not_called()


class AgyMediaReviewTests(unittest.TestCase):
    """The subscription route reviews real files; whatever it returns still faces the same strict gate."""
    MEDIA=[{'text':'Review the clip'},{'inlineData':{'mimeType':'video/mp4','data':'AAEC'}}]

    def run_review(self,tmp,stdout='',returncode=0,parts=None,raises=None):
        seen={}
        def fake(cmd,**kw):
            work=Path(cmd[cmd.index('--add-dir')+1])
            seen.update(cmd=cmd,cwd=kw['cwd'],env=kw['env'],files=sorted(f.name for f in work.iterdir()),work=work)
            if raises:raise raises
            return Mock(stdout=stdout,returncode=returncode)
        attempts=[]
        with patch.object(client,'BASE_DIR',Path(tmp)),patch.object(client.os,'access',return_value=True),\
                patch.object(client.subprocess,'run',side_effect=fake):
            result=client.agy_media_review(parts or self.MEDIA,attempts)
        return result,attempts,seen

    def test_quota_exhaustion_falls_back_to_subscription_model_with_files_not_base64(self):
        quota=Mock(ok=False,status_code=429);quota.json.return_value={'error':{'status':'RESOURCE_EXHAUSTED'}}
        envelope=json.dumps({'status':'SUCCESS','response':'```json\n{"publishable":true,"scenes":[]}\n```'})
        seen={}
        def fake(cmd,**kw):
            seen['cmd']=cmd;seen['files']=sorted(f.name for f in Path(kw['cwd']).iterdir())
            return Mock(stdout=envelope,returncode=0)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(client.os.environ,GEMINI_API_KEY='test',OPENROUTER_API_KEY=''),\
                patch.object(client,'BASE_DIR',Path(tmp)),patch.object(client.os,'access',return_value=True),\
                patch.object(client.requests,'post',return_value=quota),patch.object(client.subprocess,'run',side_effect=fake):
            diag=Path(tmp)/'attempts.json'
            result,model=client.review(self.MEDIA,diag)
            self.assertIs(result['publishable'],True);self.assertEqual(model,client.CLI_MODELS[0])
            self.assertEqual(seen['files'],['media_1.mp4'])
            self.assertNotIn('AAEC',' '.join(seen['cmd']))
            self.assertIn('media_1.mp4',seen['cmd'][2])
            record=json.loads(diag.read_text())
            self.assertEqual(record['selected_model'],model)
            self.assertEqual([a.get('http') for a in record['attempts'] if a.get('http')],[429,429])
            self.assertEqual(list((Path(tmp)/'data'/'agy_review').iterdir()),[])

    def test_read_access_is_limited_to_the_private_directory_and_everything_else_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,_,seen=self.run_review(tmp,json.dumps({'status':'SUCCESS','response':'{"ok":1}'}))
            self.assertEqual(result,({'ok':1},client.CLI_MODELS[0]))
            cmd=seen['cmd'];settings=json.loads(cmd[cmd.index('--settings')+1])['permissions']
            self.assertEqual(settings['allow'],['read_file(%s/*)'%seen['work']])
            for denied in ('write_file(*)','command(*)','read_url(*)','execute_url(*)','mcp(*)'):self.assertIn(denied,settings['deny'])
            self.assertEqual(seen['cwd'],str(seen['work']));self.assertEqual(cmd[cmd.index('--model')+1],client.CLI_MODELS[0])
            self.assertEqual(cmd[cmd.index('--effort')+1],'high')
            self.assertEqual(set(seen['env']),{'PATH','HOME','http_proxy','https_proxy'})
            self.assertFalse(seen['work'].exists())

    def test_media_positions_are_marked_in_the_prompt(self):
        parts=[{'text':'Clip 1'},{'inlineData':{'mimeType':'audio/wav','data':'AAEC'}},
               {'text':'Clip 2'},{'inlineData':{'mimeType':'audio/wav','data':'AwQF'}}]
        with tempfile.TemporaryDirectory() as tmp:
            _,_,seen=self.run_review(tmp,json.dumps({'status':'SUCCESS','response':'{}'}),parts=parts)
            prompt=seen['cmd'][2]
            self.assertEqual(seen['files'],['media_1.wav','media_2.wav'])
            self.assertLess(prompt.index('Clip 1'),prompt.index('media_1.wav'));self.assertLess(prompt.index('media_1.wav'),prompt.index('Clip 2'))
            self.assertLess(prompt.index('Clip 2'),prompt.index('media_2.wav'))

    def test_bad_answers_are_failed_attempts_never_a_pass(self):
        cases=[('not json at all',0),(json.dumps({'status':'SUCCESS','response':'I cannot view the file'}),0),
               (json.dumps({'status':'SUCCESS','response':'[1,2]'}),0),
               (json.dumps({'status':'ERROR','error':'quota exhausted'}),0),
               (json.dumps({'status':'SUCCESS','response':'{"publishable":true}'}),1)]
        for stdout,code in cases:
            with self.subTest(stdout=stdout),tempfile.TemporaryDirectory() as tmp:
                result,attempts,seen=self.run_review(tmp,stdout,code)
                self.assertIsNone(result);self.assertEqual(len(attempts),1);self.assertIn('error',attempts[0])
                self.assertFalse(seen['work'].exists())

    def test_timeout_and_unsupported_media_are_not_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,attempts,seen=self.run_review(tmp,raises=client.subprocess.TimeoutExpired('agy',1))
            self.assertIsNone(result);self.assertEqual(attempts[0]['error'],'timeout');self.assertFalse(seen['work'].exists())
        with tempfile.TemporaryDirectory() as tmp,patch.object(client,'BASE_DIR',Path(tmp)),\
                patch.object(client.os,'access',return_value=True),patch.object(client.subprocess,'run') as run:
            for parts in ([{'text':'only text'}],[{'inlineData':{'mimeType':'application/pdf','data':'AAEC'}}]):
                self.assertIsNone(client.agy_media_review(parts,[]))
            run.assert_not_called()

    def test_missing_cli_is_skipped_and_exhausted_providers_still_fail_the_review(self):
        quota=Mock(ok=False,status_code=429)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(client.os.environ,GEMINI_API_KEY='test',OPENROUTER_API_KEY=''),\
                patch.object(client,'BASE_DIR',Path(tmp)),patch.object(client.os,'access',return_value=False),\
                patch.object(client.requests,'post',return_value=quota),patch.object(client.subprocess,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'unavailable'):client.review(self.MEDIA,Path(tmp)/'attempts.json')
            run.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp,patch.dict(client.os.environ,GEMINI_API_KEY='test',OPENROUTER_API_KEY=''),\
                patch.object(client,'BASE_DIR',Path(tmp)),patch.object(client.os,'access',return_value=True),\
                patch.object(client.requests,'post',return_value=quota),\
                patch.object(client.subprocess,'run',return_value=Mock(stdout='garbage',returncode=1)):
            with self.assertRaisesRegex(RuntimeError,'unavailable'):client.review(self.MEDIA,Path(tmp)/'attempts.json')


class FeedbackTests(unittest.TestCase):
    def test_delayed_retention_keeps_the_original_age_matched_view_count(self):
        old={'url':'video','platform':'youtube','age_hours':24,'metrics':{'views':100,'observed_at':100}}
        history=feedback.record_cohorts({},[old])
        later={'url':'video','platform':'youtube','age_hours':25,'metrics':{
            'views':200,'observed_at':200,'average_percentage_viewed':80}}
        feedback.record_cohorts(history,[later])
        actual=history['video@24']
        self.assertEqual(actual['metrics']['views'],100)
        self.assertEqual(actual['metrics']['observed_at'],100)
        self.assertEqual(actual['metrics']['retention_observed_at'],200)
        self.assertEqual(actual['metrics']['average_percentage_viewed'],80)
        feedback.record_cohorts(history,[{**later,'age_hours':31,'metrics':{
            'average_percentage_viewed':99,'observed_at':300}}])
        self.assertEqual(actual['metrics']['average_percentage_viewed'],80)

    def test_missing_social_post_title_does_not_stop_the_editor(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)
            payload={'updated_at':feedback.time.time(),'brands':{'migratoria':{
                'platform_observations':{'vk':[{'title':None,'age_hours':None,'metrics':{'comments':0}}]}}}}
            (data/'content_feedback.json').write_text(json.dumps(payload))
            with patch.object(feedback,'DATA',data):brief=feedback.editorial_brief('migratoria')
            parsed=json.loads(brief.split(': ',1)[1])
            self.assertEqual(parsed['platform_observations']['vk'][0]['title'],'')
            self.assertEqual(parsed['platform_observations']['vk'][0]['metrics'],{'comments':0})

    def test_editorial_brief_keeps_every_platform_in_complete_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)
            payload={'updated_at':feedback.time.time(),'brands':{'migratoria':{'guidance':'Measured only',
                'platform_observations':{p:[{'title':'Title '*100,'age_hours':25,'metrics':{'comments':0}}] for p in ('telegram','youtube','vk','dzen','reddit')}}}}
            (data/'content_feedback.json').write_text(json.dumps(payload))
            with patch.object(feedback,'DATA',data):brief=feedback.editorial_brief('migratoria')
            parsed=json.loads(brief.split(': ',1)[1])
            self.assertEqual(set(parsed['platform_observations']),{'telegram','youtube','vk','dzen'})

    def test_dzen_impressions_are_not_views_or_the_next_articles_counters(self):
        from metrics_numbers import dzen_dashboard
        body='Заголовок А\n16 ч\n0\nдочитываний\nПоследние публикации\nЗаголовок А\n16 ч\n1\nпоказ\n100%\nCTR в ленте\n0\nдочитали\n0\nлайков\n0\nкомментариев\nЗаголовок Б\n23 ч\n99\nпоказов\n5.9%\nCTR в ленте\n4\nдочитали'
        result=dzen_dashboard(body,[{'platform':'dzen','title':'Заголовок А','url':'a'},{'platform':'dzen','title':'Заголовок Б','url':'b'}])
        self.assertEqual(result['a']['impressions'],1)
        self.assertEqual(result['a']['read_completions'],0)
        self.assertNotIn('views',result['a'])
        self.assertEqual(result['b']['read_completions'],4)
    def test_locale_counts_do_not_turn_thousands_into_single_views(self):
        self.assertEqual(feedback.number('1,346'),1346)
        self.assertEqual(feedback.number('1,3K'),1300)
        self.assertIsNone(feedback.number('—'))
    def test_missing_measurement_is_not_zero_and_different_ages_not_compared(self):
        rows=[{'brand':'wrongdev','platform':'youtube','kind':'video','title':'old','url':'old','age_hours':25,'metrics':{'views':100}},
              {'brand':'wrongdev','platform':'youtube','kind':'video','title':'fresh','url':'fresh','age_hours':1,'metrics':{'views':10}},
              {'brand':'wrongdev','platform':'youtube','kind':'video','title':'unknown','url':'unknown','age_hours':25,'metrics':{}}]
        r=feedback.analyze(rows)['wrongdev']
        self.assertEqual(r['measured_items'],2)
        self.assertEqual(r['same_age_comparisons'][0]['count'],1)
        self.assertEqual(r['same_age_comparisons'][0]['median_views'],100)
    def test_voice_assignment_survives_retries_and_balances_within_brand(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);(base/'data').mkdir()
            (base/'data/voice_experiments.json').write_text(json.dumps({'voices':['Holt','VoiceB'],'assignments':{}}))
            with patch.object(voices,'BASE_DIR',base):
                self.assertEqual(voices.choose('wrongdev','one'),'Holt')
                self.assertEqual(voices.choose('wrongdev','one'),'Holt')
                self.assertEqual(voices.choose('wrongdev','two'),'VoiceB')
                self.assertEqual(voices.choose('migratoria','other'),'Holt')


if __name__=='__main__':unittest.main()
