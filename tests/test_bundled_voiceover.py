import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import bundled_voiceover as voice
import autonomous_video_pipeline as pipeline
from script_generator import validate_script


class NarrationRepairTests(unittest.TestCase):
    def test_asr_fraction_format_preserves_exact_amount_and_negation(self):
        from native_audio import speech_tokens
        self.assertEqual(speech_tokens('в три с половиной раза'),speech_tokens('в 3,5 раза'))
        self.assertNotEqual(speech_tokens('в три с половиной раза'),speech_tokens('в 3,6 раза'))
        self.assertNotEqual(speech_tokens('не вырос в три с половиной раза'),speech_tokens('вырос в 3,5 раза'))
        self.assertNotEqual(speech_tokens('в три с половиной раза'),speech_tokens('в 3,5 процента'))

    def test_asr_brand_spelling_is_the_same_spoken_name(self):
        from native_audio import speech_tokens
        self.assertEqual(speech_tokens('Рейтерс заявляет'),speech_tokens('Reuters заявляет'))

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.run=self.root/'runs/job';self.run.mkdir(parents=True)
        (self.run/'narration.asr.json').write_text(json.dumps({'text':'Неверно произнесённая фраза'}))
        self.script=validate_script({'source_text':'Заявление можно подать до переезда.',
            'scenes':[{'video_prompt_en':'A hand passes a paper folder through a service window beside a closed suitcase. No people or faces.',
                'voiceover_ru':'Заявление подают до переезда','on_screen_text_ru':'До переезда','edit_note_ru':'Склейка'} for _ in range(3)],
            'youtube_meta':{'title':'Подача до переезда','description':'Подробности','tags':[]}})
        self.script['narration_voice']='Holt'  # No production experiment assignment in a repair unit test.
    def tearDown(self):self.tmp.cleanup()

    def test_rewrite_is_reviewed_archived_and_verified_again(self):
        self.script['narration_take']=2
        original=copy.deepcopy(self.script)
        with patch.object(pipeline,'OUTPUT_DIR',self.root),patch.object(voice,'_prepare',side_effect=[voice.SpeechMismatch('mismatch'),['verified']]) as prepare,patch('migratoria_editor.llm',return_value={'voiceovers':['Документы подают до поездки','Документы подают до поездки','ignored CTA']}),patch('script_review.audit',return_value={'supported':True,'allowed_topic':True}) as audit:
            self.assertEqual(voice.prepare(self.script,self.run,'job'),['verified'])
        self.assertEqual(prepare.call_count,2);audit.assert_called_once()
        self.assertEqual(self.script['narration_revision'],1)
        self.assertEqual(self.script['scenes'][-1]['voiceover_ru'],original['scenes'][-1]['voiceover_ru'])
        self.assertTrue((self.run/'rejected_narration_0/narration.asr.json').exists())

    def test_exact_script_gets_one_fresh_take_without_changing_words(self):
        original=self.script['voiceover_ru']
        with patch.object(pipeline,'OUTPUT_DIR',self.root),patch.object(voice,'_prepare',side_effect=[voice.SpeechMismatch('mismatch'),['verified']]),patch('migratoria_editor.llm') as rewrite:
            self.assertEqual(voice.prepare(self.script,self.run,'job'),['verified'])
        self.assertEqual(self.script['voiceover_ru'],original)
        self.assertEqual(self.script['narration_take'],2)
        rewrite.assert_not_called()

    def test_scene_too_long_for_its_clip_holds_the_source_instead_of_retrying_forever(self):
        voice.check_scene_fits(7.8)
        for duration in (8.38,1.0):
            with self.assertRaises(voice.NarrationNeedsReview):voice.check_scene_fits(duration)
    def test_existing_visual_submission_prevents_rewriting_its_narration(self):
        routes=self.root/'generation_routes';routes.mkdir()
        (routes/'job_s1.json').write_text('{}')
        with patch.object(pipeline,'OUTPUT_DIR',self.root),patch.object(voice,'_prepare',side_effect=voice.SpeechMismatch('mismatch')),patch('migratoria_editor.llm') as rewrite:
            with self.assertRaisesRegex(RuntimeError,'visual generation already started'):voice.prepare(self.script,self.run,'job')
        rewrite.assert_not_called()

    def test_revision_limit_prevents_unbounded_voice_requests(self):
        self.script['narration_revision']=2
        with patch.object(pipeline,'OUTPUT_DIR',self.root),patch.object(voice,'_prepare',side_effect=voice.SpeechMismatch('mismatch')),patch('migratoria_editor.llm') as rewrite:
            with self.assertRaisesRegex(RuntimeError,'bounded rewrites exhausted'):voice.prepare(self.script,self.run,'job')
        rewrite.assert_not_called()

    def test_second_repair_rejected_for_invented_facts_holds_the_source(self):
        self.script['narration_take']=2
        (self.run/'narration_repair_state.json').write_text(json.dumps({'attempts':1}))
        original=copy.deepcopy(self.script)
        with patch.object(pipeline,'OUTPUT_DIR',self.root),patch.object(voice,'_prepare',side_effect=voice.SpeechMismatch('mismatch')),\
                patch('migratoria_editor.llm',return_value={'voiceovers':['Документы подают до поездки','Документы подают до поездки','ignored CTA']}),\
                patch('script_review.audit',return_value={'supported':False,'allowed_topic':True,'reason':'Invented quantity'}):
            with self.assertRaisesRegex(voice.NarrationNeedsReview,'factual review'):voice.prepare(self.script,self.run,'job')
        self.assertEqual(self.script,original)


if __name__=='__main__':unittest.main()
