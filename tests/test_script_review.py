import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import script_review


class ScriptReviewTests(unittest.TestCase):
    def test_audio_bookkeeping_reuses_the_same_factual_decision(self):
        original={'source_text':'A specific supported claim','voiceover_ru':'Исходная речь',
            'scenes':[{'voiceover_ru':'Исходная речь','video_prompt_en':'A laptop opens.'}],
            'youtube_meta':{'title':'Исходный заголовок'}}
        aligned=copy.deepcopy(original)
        aligned.update(narration_voice='Knox',narration_take=2,narration_revision=1)
        aligned['scenes'][0]['_speech_words']=[{'word':'Исходная','start':0,'end':1}]
        with tempfile.TemporaryDirectory() as folder,patch('migratoria_editor.llm',return_value={
                'supported':False,'allowed_topic':True,'reason':'Unsupported claim'}) as llm:
            first=script_review.audit(original,folder)
            second=script_review.audit(aligned,folder)
            self.assertEqual(first,second)
            self.assertEqual(llm.call_count,1)
            with self.assertRaises(ValueError):script_review.require_pass(second)

    def test_actual_narration_change_requires_a_fresh_review(self):
        script={'voiceover_ru':'Двенадцать часов','scenes':[{'voiceover_ru':'Двенадцать часов'}]}
        changed=copy.deepcopy(script)
        changed['scenes'][0]['voiceover_ru']='Шесть часов'
        with tempfile.TemporaryDirectory() as folder,patch('migratoria_editor.llm',side_effect=lambda *a,**kw:{
                'supported':True,'allowed_topic':True}) as llm:
            first=script_review.audit(script,folder)
            second=script_review.audit(changed,folder)
            self.assertNotEqual(first['script_sha256'],second['script_sha256'])
            self.assertEqual(llm.call_count,2)

    def test_publication_rejects_a_failed_script_even_with_a_visual_pass(self):
        import video_quality
        from bundled_voiceover import NarrationNeedsReview
        with self.assertRaisesRegex(NarrationNeedsReview,'Script factual'):
            video_quality.require_artifact({'script_review':{'supported':False,'allowed_topic':True},
                'visual_review':{'publishable':True}})


if __name__=='__main__':unittest.main()
