"""Regression checks for stale-video reuse and speech/display separation."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import script_generator as generator
import autonomous_video_pipeline as pipeline


def package():
    return {'scenes': [
        {'video_prompt_en': 'Close-up of a monitor. A cursor selects a subscription card while the camera pushes in. No avatars are visible.',
         'voiceover_ru': 'Чат Джи Пи Ти открывает нужный раздел на экране',
         'on_screen_text_ru': 'ChatGPT 4.1', 'edit_note_ru': 'Резкая склейка'},
        {'video_prompt_en': 'A laptop display shows two code panels merging into one concise function, lit by soft blue light.',
         'voiceover_ru': 'Теперь сравниваем результат и выбираем подходящий вариант',
         'on_screen_text_ru': 'OpenAI', 'edit_note_ru': 'Приближение'},
        {'video_prompt_en': 'A smartphone with a clean messaging interface floats over a dark desk as the camera gently pushes in.',
         'voiceover_ru': 'Полный гайд по ссылке', 'on_screen_text_ru': '@WrongDevp', 'edit_note_ru': 'Финал'}],
        'youtube_meta': {'title': 'ChatGPT 4.1 от OpenAI: что изменилось',
                         'description': 'Гайд: https://t.me/WrongDevp', 'tags': ['ChatGPT', 'OpenAI']}}


class ScriptContractTests(unittest.TestCase):
    def test_short_rate_limit_retries_the_rejected_request_and_saves_a_validated_script(self):
        quota=Mock(status_code=429,headers={'retry-after':'0.1'})
        quota.json.return_value={'error':{'message':'Please try again in 0.1s'}}
        good=Mock(status_code=200)
        good.json.return_value={'choices':[{'message':{'content':json.dumps(package())}}]}
        with tempfile.TemporaryDirectory() as tmp,patch.dict(generator.os.environ,GROQ_API_KEY='test',MEDIA_BRAND='wrongdev'),patch.object(generator,'MODELS',['openai/gpt-oss-120b']),patch.object(generator,'OUTPUT_DIR',Path(tmp)/'output'),patch.object(generator.requests,'post',side_effect=[quota,good]) as post,patch.object(generator.time,'sleep') as sleep,patch('script_review.audit',return_value={'supported':True,'allowed_topic':True}),patch('content_feedback.editorial_brief',return_value=''):
            result=generator.generate_shorts_script('Approved source',Path(tmp)/'script')
            self.assertTrue((Path(tmp)/'script/script.json').exists())
        self.assertEqual(post.call_count,2);sleep.assert_called_once_with(1.1)
        self.assertRegex(result['voiceover_ru'],r'^[А-Яа-яЁё ]+$')

    def test_airline_and_wifi_names_are_spelled_for_russian_speech(self):
        self.assertEqual(generator.sanitize_voiceover('KLM подключает Wi‑Fi через Starlink'),
            'Ка Эл Эм подключает вай фай через Старлинк')
        self.assertEqual(generator.display_text('Ка Эл Эм и Старлинк'),'KLM и Starlink')
    def test_display_brands_and_versions_survive_speech_processing(self):
        result = generator.validate_script(package())
        self.assertEqual(result['youtube_meta']['title'], 'ChatGPT 4.1 от OpenAI: что изменилось')
        self.assertEqual(result['scenes'][0]['on_screen_text_ru'], 'ChatGPT 4.1')
        self.assertRegex(result['voiceover_ru'], r'^[А-Яа-яЁё ]+$')
        self.assertIn('ссылка в описании', result['scenes'][-1]['voiceover_ru'])
        self.assertIn('@WrongDevp', result['scenes'][-1]['on_screen_text_ru'])

    def test_unresolved_numbers_are_rejected_without_changing_the_claim(self):
        with self.assertRaises(ValueError):
            generator.sanitize_voiceover('Цена 123 рубля')

    def test_positive_avatar_instruction_is_rejected(self):
        data = package()
        data['scenes'][0]['video_prompt_en'] = 'A pixel-art avatar in a blue hoodie points to a monitor and smiles at the camera.'
        with self.assertRaises(ValueError):
            generator.validate_script(data)

    def test_russian_visual_instructions_are_rejected(self):
        data = package()
        data['scenes'][0]['video_prompt_en'] += ' Скажи привет'
        with self.assertRaises(ValueError):
            generator.validate_script(data)


class FreshVideoTests(unittest.TestCase):
    def test_failed_generation_never_returns_old_default_video(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'generated_flow_video.mp4').write_bytes(b'old-video')
            (root / 'flow_last_run.json').write_text(json.dumps({'status': 'completed', 'video_file': str(root / 'generated_flow_video.mp4')}))
            with patch.object(pipeline, 'OUTPUT_DIR', root), patch.object(pipeline.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
                with self.assertRaises(RuntimeError):
                    pipeline.generate_flow_video_file('new scene', 'Title', 'new-request')

    def test_stale_receipt_cannot_be_reused_for_a_new_scene(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'flow_new-request.json').write_text('{}')
            with patch.object(pipeline, 'OUTPUT_DIR', root), patch.object(pipeline.subprocess, 'run') as submit:
                with self.assertRaises(RuntimeError):
                    pipeline.generate_flow_video_file('new scene', 'Title', 'new-request')
                submit.assert_not_called()

    def test_short_clip_is_rejected_instead_of_looped(self):
        with patch.object(pipeline, 'probe', return_value={'format': {'duration': '4.0'}}), patch.object(pipeline, 'docker_command') as render:
            with self.assertRaises(RuntimeError):
                pipeline.render_scene(Path('new.mp4'), Path('voice.mp3'), Path('subs.ass'), 6.0, Path('short.mp4'))
            render.assert_not_called()

    def test_resume_rejects_an_asset_that_existed_before_this_request(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'flow_job-s1.json').write_text(json.dumps({
                'run_id': 'job-s1', 'status': 'completed',
                'prompt_sha256': hashlib.sha256(b'new scene').hexdigest(),
                'media_id': '/image/old-asset', 'baseline_media_ids': ['/image/old-asset']}))
            with patch.object(pipeline, 'OUTPUT_DIR', root), patch.object(pipeline.subprocess, 'run') as submit:
                with self.assertRaises(RuntimeError):
                    pipeline.generate_flow_video_file('new scene', 'Title', 'job-s1', resume=True)
                submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
