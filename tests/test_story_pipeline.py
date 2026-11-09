import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import story_pipeline as sp
import story_worker as sw


def scene(n, line='Я должен успеть до того, как погаснет свет', speaker='hero'):
    return {'id': n, 'characters': ['hero'], 'speaker': speaker if line else None, 'line_ru': line,
            'video_prompt_en': ('The hero stands in a dim corner shop at night, looks up at a flickering security monitor, '
                                'then slowly steps back as the screen shows him breaking a glass cabinet; the camera pushes in '
                                'on his worried face while warm light from the street spills across the floor and shelves.')}


def script():
    return {'title_ru': 'Камера показала мне будущее', 'logline_ru': 'Он хочет не стать вором, но должен спасти ребёнка.',
            'description_ru': 'Одна минута будущего на экране магазина. Что вы сделаете, когда увидите себя?',
            'hashtags_ru': ['#история', '#анимация', '#выбор'],
            'characters': [{'id': 'hero', 'name_ru': 'Артём', 'voice': 'man',
                            'look_en': 'a tall thin man in his thirties with messy dark hair, a mustard raincoat and a red scarf'}],
            'scenes': [scene(1), scene(2), scene(3, ''), scene(4), scene(5)],
            'planted_detail': {'scene': 1, 'payoff_scene': 5, 'detail_en': 'red strap of the bag'}}


class Validation(unittest.TestCase):
    def test_a_good_script_passes(self):
        self.assertEqual(sp.validate(script())['title_ru'], 'Камера показала мне будущее')

    def test_digits_or_cyrillic_in_prompts_and_digits_in_speech_are_rejected(self):
        for change in (lambda s: s['scenes'][0].update(video_prompt_en=s['scenes'][0]['video_prompt_en'] + ' at 3 pm'),
                       lambda s: s['scenes'][0].update(video_prompt_en=s['scenes'][0]['video_prompt_en'] + ' ночью'),
                       lambda s: s['scenes'][1].update(line_ru='Осталось 5 минут'),
                       lambda s: s['scenes'][1].update(line_ru='слово ' * 17),
                       lambda s: s.update(scenes=s['scenes'][:4]),
                       lambda s: s['scenes'][1].update(speaker='ghost'),
                       lambda s: s.update(planted_detail={'scene': 4, 'payoff_scene': 2})):
            value = script();change(value)
            with self.assertRaises(ValueError):sp.validate(value)

    def test_too_few_spoken_lines_is_rejected(self):
        value = script()
        for sc in value['scenes'][:3]:sc.update(line_ru='', speaker=None)
        with self.assertRaises(ValueError):sp.validate(value)

    def test_every_clip_request_repeats_the_fixed_style_and_character_look(self):
        value = script()
        prompt = sp.full_prompt(value, value['scenes'][0])
        self.assertTrue(prompt.startswith(sp.STYLE_EN))
        self.assertIn(value['characters'][0]['look_en'], prompt)

    def test_scene_length_follows_speech_within_one_clip(self):
        self.assertEqual(sp.scene_seconds(0), sp.MIN_SCENE)
        self.assertEqual(sp.scene_seconds(30), sp.CLIP_SECONDS)
        self.assertAlmostEqual(sp.scene_seconds(4.0), 5.0)


class Premises(unittest.TestCase):
    def test_seeds_in_order_then_a_free_premise(self):
        with patch('growth_strategy.topic_hint', return_value={}), patch('growth_strategy.context', return_value={'prompt': '', 'receipt': {}}):
            first, _ = sp.pick_premise({'used': []})
            self.assertEqual(first['id'], 'seed-witness')
            last, _ = sp.pick_premise({'used': [s['id'] for s in sp.SEEDS]})
            self.assertEqual(last['source'], 'free')

    def test_an_active_analyst_direction_comes_first(self):
        idea = {'id': 'abc', 'name_ru': 'Этаж тишины', 'original_angle_en': 'A quiet floor', 'mechanic_en': 'm'}
        with patch('growth_strategy.topic_hint', return_value=idea), \
                patch('growth_strategy.context', return_value={'prompt': 'direction', 'receipt': {'trial_id': 't'}}):
            premise, guidance = sp.pick_premise({'used': []})
        self.assertEqual(premise['source'], 'growth_strategy')
        self.assertEqual(guidance['receipt'], {'trial_id': 't'})


class Worker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.runs = root / 'output/story_runs'
        for target, name, value in ((sp, 'RUNS', self.runs), (sp, 'DATA', root / 'data/storywobble'),
                                    (sw, 'STATE', root / 'data/storywobble/worker.json'), (sw, 'LOCK', root / 'data/storywobble/.lock'),
                                    (sw, 'CONFIG', root / 'data/media_brands.json')):
            p = patch.object(target, name, value);p.start();self.addCleanup(p.stop)
        self.cfg = {'story_enabled': True, 'backlog_target': 1, 'publishing': {
            'youtube': {'enabled': True, 'channel_id': 'UC', 'channel_title': 'S', 'author_url': 'u', 'video_interval_seconds': 86400},
            'instagram': {'enabled': True, 'target': 'storywobble', 'video_interval_seconds': 86400}}}
        sp.save(root / 'data/media_brands.json', {'storywobble': self.cfg})

    def episode(self, name, published=None, at=0):
        run = self.runs / name;run.mkdir(parents=True)
        sp.save(run / 'script.json', script())
        sp.save(run / 'manifest.json', {'run_id': name, 'status': 'ready', 'published': published or {}, 'completed_at': at})
        return run

    def test_one_episode_per_platform_per_interval_and_receipts_are_kept(self):
        self.episode('a');self.episode('b')
        sent = []
        with patch.object(sw, 'publish_one', side_effect=lambda p, m, s, c: sent.append((p, m['run_id'])) or {'status': 'published', 'url': 'https://x/' + p}), \
                patch.object(sw, '_notify'):
            self.assertEqual(sw.publish_due(self.cfg, {}, lambda *_: None), 2)
            self.assertEqual(sw.publish_due(self.cfg, {}, lambda *_: None), 0)
        self.assertEqual(sorted(sent), [('instagram', 'a'), ('youtube', 'a')])
        manifest = sp.load(self.runs / 'a/manifest.json')
        self.assertEqual(manifest['status'], 'published')
        self.assertEqual(manifest['published']['youtube']['url'], 'https://x/youtube')

    def test_a_failed_upload_waits_and_is_not_marked_published(self):
        self.episode('a')
        state = {}
        with patch.object(sw, 'publish_one', side_effect=RuntimeError('studio')), patch.object(sw, '_notify'):
            self.assertEqual(sw.publish_due(self.cfg, state, lambda *_: None), 0)
        self.assertGreater(state['retry']['youtube'], time.time())
        self.assertEqual(sp.load(self.runs / 'a/manifest.json')['published'], {})

    def test_a_missing_login_waits_hours_not_minutes(self):
        self.episode('a')
        state = {}
        class NeedsLogin(Exception):pass
        with patch.object(sw, 'publish_one', side_effect=NeedsLogin('storywobble is not visible')), patch.object(sw, '_notify'):
            sw.publish_due(self.cfg, state, lambda *_: None)
        self.assertGreater(state['retry']['instagram'], time.time() + 5 * 3600)

    def test_production_waits_while_an_episode_is_unpublished(self):
        self.episode('a', {'youtube': {'url': 'u', 'at': 1}})
        self.assertFalse(sw.need_production(self.cfg, {}))
        sp.save(self.runs / 'a/manifest.json', {'run_id': 'a', 'status': 'published',
                                                'published': {'youtube': {'url': 'u'}, 'instagram': {'url': 'i'}}})
        self.assertTrue(sw.need_production(self.cfg, {}))
        self.assertFalse(sw.need_production(self.cfg, {'produce_retry_after': time.time() + 60}))
        sp.save(self.runs / 'a/manifest.json', {'run_id': 'a', 'status': 'ready', 'published': {'youtube': {'url': 'u'}}})
        self.assertFalse(sw.need_production(self.cfg, {}))
        self.assertTrue(sw.need_production(self.cfg, {'retry': {'instagram': time.time() + 6 * 3600}}))

    def test_disabled_brand_does_nothing(self):
        sp.save(sw.CONFIG, {'storywobble': {**self.cfg, 'story_enabled': False}})
        self.assertEqual(sw.tick(lambda *_: None), {'status': 'disabled'})

    def test_an_unreviewed_or_changed_file_cannot_be_published(self):
        run = self.runs / 'r';run.mkdir(parents=True)
        video = run / 'short.mp4';video.write_bytes(b'video')
        review = {'publishable': True, 'score_1_to_5': 4, 'video_sha256': sp.digest(video),
                  'scenes': [{'id': 1, 'matches_narration': True, 'meaningful_action_or_edit': True}]}
        manifest = {'status': 'ready', 'system': sp.SYSTEM, 'video': str(video), 'sha256': sp.digest(video),
                    'visual_review': review, 'scene_count': 1}
        self.assertEqual(sp.require_artifact(manifest), video)
        video.write_bytes(b'changed')
        with self.assertRaises(sp.StoryError):sp.require_artifact(manifest)
        video.write_bytes(b'video')
        with self.assertRaises(Exception):sp.require_artifact({**manifest, 'visual_review': {**review, 'score_1_to_5': 2}})


class Analytics(unittest.TestCase):
    def test_published_story_urls_reach_the_analytics_inventory(self):
        import content_feedback as cf
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sp.save(root / 'output/story_runs/sw-1/manifest.json', {'run_id': 'sw-1', 'title': 'T', 'gate': {'duration': 40},
                    'published': {'youtube': {'url': 'https://youtu.be/abc', 'at': 5}}})
            with patch.object(cf, 'BASE_DIR', root):
                items = [i for i in cf.inventory() if i.get('brand') == 'storywobble']
        self.assertEqual(items, [{'brand': 'storywobble', 'platform': 'youtube', 'kind': 'video', 'run_id': 'sw-1',
                                  'source_id': 'story-sw-1', 'title': 'T', 'url': 'https://youtu.be/abc', 'published_at': 5, 'duration': 40}])


if __name__ == '__main__':unittest.main()
