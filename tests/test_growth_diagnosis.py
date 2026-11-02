"""The diagnosis states sample sizes, separates hints from findings, and never ranks videos that are too young."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import growth_diagnosis as gd

NOW = 1_800_000_000


def item(n, views, hours, title=None, **extra):
    return {'brand': 'b', 'platform': 'youtube', 'url': f'https://youtu.be/v{n}', 'title': title or f'video {n}', 'published_at': NOW - hours * 3600,
        'metrics': {'views': views, 'likes': extra.get('likes', 20), 'comments': extra.get('comments', 1), 'duration_s': extra.get('duration', 16)}}


class BaselineTests(unittest.TestCase):
    def test_too_few_mature_videos_means_no_verdict(self):
        out = gd.build_brand([item(1, 100, 60), item(2, 50, 70), item(3, 900, 5)], {}, NOW)
        self.assertEqual(out['status'], 'insufficient_baseline')
        self.assertEqual(out['mature_videos'], 2)

    def test_scores_use_the_median_of_mature_videos_only(self):
        items = [item(i, v, 72) for i, v in enumerate([100, 200, 300, 400, 500])] + [item(9, 5000, 6)]
        out = gd.build_brand(items, {}, NOW)
        self.assertEqual(out['baseline']['median_views'], 300)
        self.assertEqual(out['videos_scored'], 5)                      # the six-hour-old video is not ranked
        self.assertEqual([s['views'] for s in out['early_surges']], [5000])
        self.assertTrue(any('early surge' in h for h in out['hypotheses']))

    def test_a_fresh_video_without_a_baseline_surge_is_not_flagged(self):
        items = [item(i, 100, 72) for i in range(4)] + [item(9, 150, 6)]
        self.assertEqual(gd.build_brand(items, {}, NOW)['early_surges'], [])


class FeatureTests(unittest.TestCase):
    def rows(self, with_views, without_views):
        out = []
        for i, v in enumerate(with_views):
            out.append({'url': f'a{i}', 'title': 'Как 5 привычек', 'score': v, 'features': {'title_has_number': True}})
        for i, v in enumerate(without_views):
            out.append({'url': f'b{i}', 'title': 'Привычки', 'score': v, 'features': {'title_has_number': False}})
        return out

    def test_a_feature_needs_enough_videos_on_both_sides_to_be_a_finding(self):
        small = gd.lift(self.rows([3, 4], [1, 1, 1, 1]), 'title_has_number')
        self.assertEqual(small['status'], 'hint')
        big = gd.lift(self.rows([3, 4, 2, 3], [1, 1, 1, 1]), 'title_has_number')
        self.assertEqual((big['status'], big['ratio']), ('finding', 3.0))
        self.assertEqual(gd.lift(self.rows([2], [1, 1, 1, 1]), 'title_has_number')['status'], 'too_few')

    def test_title_features_are_read_from_the_title_and_time(self):
        f = gd.features({'title': 'Почему ты устаёшь? 7 причин', 'published_at': NOW - NOW % 86400 + 17 * 3600, 'metrics': {'duration_s': 22}})
        self.assertTrue(f['title_has_number'] and f['title_is_question'] and f['title_starts_how_why'] and f['duration_over_20s'] and f['posted_evening_utc_15_21'])

    def test_year_numbers_are_not_title_words(self):
        self.assertNotIn('2026', gd.words('Правила 2026 года'))


class FlagTests(unittest.TestCase):
    def test_flags_quote_the_numbers_behind_them(self):
        row = {'views': 1000, 'likes': 3, 'comments': 0}
        flags = gd.video_flags(row, {'stayed_to_watch_pct': 41.0, 'subscribers_gained': 0, 'traffic_sources': {'YouTube search': 82.4, 'Shorts feed': 10}})
        text = ' | '.join(flags)
        self.assertIn('weak_hook: only 41%', text)
        self.assertIn('low_likes: 3/1000', text)
        self.assertIn('no_comments', text)
        self.assertIn('no_follow', text)
        self.assertIn('82% from YouTube search', text)

    def test_missing_overview_data_raises_no_hook_flag(self):
        self.assertEqual(gd.video_flags({'views': 50, 'likes': 5, 'comments': 1}, {}), [])


class FunnelAndRunTests(unittest.TestCase):
    def test_subscribers_per_thousand_views_need_two_observations(self):
        rows = [{'k': 'channel:UC1', 't': NOW, 'views': 10000, 'subscribers': 20}]
        self.assertEqual(gd.channel_funnel(rows, 'UC1')['status'], 'need_more_observations')
        rows.append({'k': 'channel:UC1', 't': NOW + 86400, 'views': 12000, 'subscribers': 24})
        out = gd.channel_funnel(rows, 'UC1')
        self.assertEqual((out['subscribers_gained'], out['subscribers_per_1000_views'], out['since_hours']), (4, 2.0, 24.0))

    def test_run_writes_a_file_and_summarises_in_russian(self):
        latest = {'items': [{**item(i, v, 72), 'brand': 'b'} for i, v in enumerate([100, 200, 300, 400])]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(gd, 'OUT', Path(tmp) / 'd.json'):
            result = gd.run(now=NOW, latest=latest, overview={}, series=[], configs={'b': {'youtube_channel_id': 'UC1'}})
            self.assertEqual(json.loads((Path(tmp) / 'd.json').read_text())['brands']['b']['baseline']['median_views'], 250)
            self.assertIn('медиана 250 просмотров (n=4)', gd.digest_lines(result)[0])


if __name__ == '__main__':unittest.main()
