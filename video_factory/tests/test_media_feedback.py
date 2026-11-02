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
            self.assertEqual(set(parsed['platform_observations']),{'telegram','youtube','vk','dzen','reddit'})

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
