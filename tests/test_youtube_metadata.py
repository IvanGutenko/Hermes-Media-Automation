"""YouTube refuses angle brackets in metadata; a '>' once left a Studio upload stuck for good."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import youtube_uploader as y


class AngleBracketTests(unittest.TestCase):
    def test_text_keeps_its_meaning_without_the_forbidden_characters(self):
        self.assertEqual(y.youtube_text('в Settings > Usage'), 'в Settings → Usage')
        self.assertEqual(y.youtube_text('a < b'), 'a ← b')
        self.assertEqual(y.youtube_text('чистый текст'), 'чистый текст')

    def test_tags_lose_the_characters_and_empty_tags_disappear(self):
        self.assertEqual(y.youtube_tags(['OpenAI', '<b>', '>', 'Codex>']), ['OpenAI', 'b', 'Codex'])
        self.assertEqual(y.youtube_tags(None), [])

    def test_the_studio_job_sent_to_the_browser_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(y, 'BASE_DIR', Path(tmp)):
            (Path(tmp) / 'output').mkdir()
            video = Path(tmp) / 'output' / 'short.mp4'
            video.write_bytes(b'video')
            captured = {}
            def fake_run(command, **kwargs):
                captured['job'] = json.loads((Path(tmp) / 'output' / 'studio_jobs' / Path(command[-1]).name).read_text())
                return subprocess.CompletedProcess(command, 0, stdout='{"status": "published"}\n', stderr='')
            cfg = {'youtube_channel_id': 'UC1', 'youtube_channel_title': 'T', 'youtube_author_url': 'u'}
            with patch.object(y, 'run_browser', side_effect=fake_run):
                result = y.upload_video_studio(video, 'Лимиты <новые>', 'Меню Settings > Usage', ['AI', '<x>'], cfg, 'wrongdev')
        self.assertEqual(result, {'status': 'published'})
        self.assertEqual(captured['job']['title'], 'Лимиты ←новые→')
        self.assertEqual(captured['job']['description'], 'Меню Settings → Usage')
        self.assertEqual(captured['job']['tags'], ['AI', 'x'])


if __name__ == '__main__':
    unittest.main()
