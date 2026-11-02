"""Browser-free YouTube counters and the Studio overview parser."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import studio_overview
import yt_stats

OVERVIEW = '''Video analytics
Skip navigation
Your video
Как скрытый markdown в ~/Downloads украл токены у Claude Code и Codex
Video analytics
Advanced mode
Overview
Oct 29, 2026 — Now
Since published
Great job! Views are 6.7 times higher than your other Shorts and your Short got shared more.
Views
Views
1.3K
1.1K more than usual
Total views since the video was published.
Subscribers
—
Total change in subscriber count from people viewing this video since it was published.
This video
Typical performance
See more
Interest in your content • Since published
Key moments for audience retention
Stayed to watch
54.0%
Average view duration
0:18
0:00 / 0:00
This video
Typical retention not available
Chart guide
See more
Realtime
Updating live
17
Views · Last 48 hours
Top traffic sources
Views
YouTube search
82.4%
Shorts feed
17.6%
See more
'''


class OverviewTests(unittest.TestCase):
    def test_a_rendered_page_gives_retention_duration_and_sources(self):
        parsed = studio_overview.parse(OVERVIEW)
        self.assertEqual(parsed['views'], 1300)
        self.assertEqual(parsed['stayed_to_watch_pct'], 54.0)
        self.assertEqual(parsed['average_view_duration_s'], 18)
        self.assertEqual(parsed['traffic_sources'], {'YouTube search': 82.4, 'Shorts feed': 17.6})
        self.assertTrue(studio_overview.complete(parsed))

    def test_a_dash_is_unknown_not_zero(self):
        self.assertNotIn('subscribers_gained', studio_overview.parse(OVERVIEW))
        self.assertEqual(studio_overview.parse(OVERVIEW.replace('Subscribers\n—', 'Subscribers\n+3'))['subscribers_gained'], 3)

    def test_a_half_rendered_page_is_not_complete(self):
        half = 'Video analytics\nSkip navigation\nAsk Studio\nCreate\nVideo analytics\nAdvanced mode\n'
        self.assertEqual(studio_overview.parse(half), {})
        self.assertFalse(studio_overview.complete({}))


class ApiTests(unittest.TestCase):
    def test_video_ids_come_from_every_youtube_link_shape(self):
        self.assertEqual(yt_stats.video_id('https://youtu.be/mnf5Nn8kPYg'), 'mnf5Nn8kPYg')
        self.assertEqual(yt_stats.video_id('https://www.youtube.com/watch?v=mnf5Nn8kPYg&t=3'), 'mnf5Nn8kPYg')
        self.assertEqual(yt_stats.video_id('https://youtube.com/shorts/mnf5Nn8kPYg'), 'mnf5Nn8kPYg')
        self.assertIsNone(yt_stats.video_id('https://t.me/WrongDevp/12'))

    def test_durations_in_iso_form(self):
        self.assertEqual(yt_stats.parse_duration('PT16S'), 16)
        self.assertEqual(yt_stats.parse_duration('PT1M16S'), 76)
        self.assertEqual(yt_stats.parse_duration('PT1H'), 3600)
        self.assertIsNone(yt_stats.parse_duration('P0D'))

    def test_a_hidden_counter_is_none_never_zero(self):
        session = Mock()
        session.get.return_value = Mock(ok=True, json=Mock(return_value={'items': [{
            'id': 'abcdefghijk', 'statistics': {'viewCount': '12', 'likeCount': '3'},
            'contentDetails': {'duration': 'PT20S'}, 'snippet': {'title': 'T', 'channelId': 'UC1'}}]}))
        row = yt_stats.videos(session, ['abcdefghijk'], now=5)['https://youtu.be/abcdefghijk']
        self.assertEqual((row['views'], row['likes'], row['duration_s']), (12, 3, 20))
        self.assertIsNone(row['comments'])

    def test_an_api_failure_raises_instead_of_returning_empty_counters(self):
        session = Mock()
        session.get.return_value = Mock(ok=False, status_code=403)
        with self.assertRaises(RuntimeError):yt_stats.videos(session, ['abcdefghijk'])

    def test_hidden_subscribers_are_unknown(self):
        session = Mock()
        session.get.return_value = Mock(ok=True, json=Mock(return_value={'items': [
            {'id': 'UC1', 'statistics': {'subscriberCount': '7', 'viewCount': '100', 'videoCount': '3'}},
            {'id': 'UC2', 'statistics': {'hiddenSubscriberCount': True, 'viewCount': '1'}}]}))
        result = yt_stats.channels(session, ['UC1', 'UC2'], now=1)
        self.assertEqual(result['UC1']['subscribers'], 7)
        self.assertIsNone(result['UC2']['subscribers'])


class SeriesTests(unittest.TestCase):
    def test_a_quiet_counter_costs_one_line_an_hour_and_a_change_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's.jsonl'
            self.assertEqual(yt_stats.append_series(path, {'u': {'views': 5}}, now=1000), 1)
            self.assertEqual(yt_stats.append_series(path, {'u': {'views': 5}}, now=1900), 0)   # unchanged, 15 min
            self.assertEqual(yt_stats.append_series(path, {'u': {'views': 6}}, now=1500), 0)   # changed but under 10 min? no: 500s
            self.assertEqual(yt_stats.append_series(path, {'u': {'views': 6}}, now=1700), 1)   # changed, 700 s later
            self.assertEqual(yt_stats.append_series(path, {'u': {'views': 6}}, now=1700 + 3400), 1)  # an hour later
            rows = yt_stats.read_series(path, 'u')
            self.assertEqual([r['views'] for r in rows], [5, 6, 6])

    def test_missing_values_are_not_written_as_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's.jsonl'
            yt_stats.append_series(path, {'u': {'views': 5, 'likes': None}}, now=1)
            self.assertNotIn('likes', json.loads(path.read_text().splitlines()[0]))


if __name__ == '__main__':unittest.main()
