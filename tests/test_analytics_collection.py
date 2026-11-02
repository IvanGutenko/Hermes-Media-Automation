"""The analytics tick must not depend on the shared browser lease for its core counters."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import content_feedback as cf

NOW = 1_800_000_000


def video(url, hours_old):
    return {'brand': 'wrongdev', 'platform': 'youtube', 'kind': 'video', 'url': url, 'title': url, 'published_at': NOW - hours_old * 3600}


class OverviewSchedulingTests(unittest.TestCase):
    def test_a_young_video_waits_and_a_new_one_is_read_first(self):
        items = [video('https://youtu.be/young', 1), video('https://youtu.be/fresh', 10), video('https://youtu.be/new', 22), video('https://youtu.be/seen', 30)]
        store = {'https://youtu.be/seen': {'snapshots': [{'observed_at': NOW - 3600}]}}
        self.assertEqual(cf.overview_targets(items, store, NOW), ['https://youtu.be/new'])

    def test_a_page_studio_was_not_ready_for_is_not_retried_every_hour(self):
        item = video('https://youtu.be/a', 30)
        tried = {'https://youtu.be/a': {'snapshots': [], 'last_attempt': NOW - 3600}}
        self.assertEqual(cf.overview_targets([item], tried, NOW), [])
        tried['https://youtu.be/a']['last_attempt'] = NOW - 6 * 3600
        self.assertEqual(cf.overview_targets([item], tried, NOW), ['https://youtu.be/a'])

    def test_a_due_refresh_follows_the_age_dependent_interval(self):
        item = video('https://youtu.be/a', 30)
        fresh = {'https://youtu.be/a': {'snapshots': [{'observed_at': NOW - 4 * 3600}]}}
        stale = {'https://youtu.be/a': {'snapshots': [{'observed_at': NOW - 6 * 3600}]}}
        self.assertEqual(cf.overview_targets([item], fresh, NOW), [])
        self.assertEqual(cf.overview_targets([item], stale, NOW), ['https://youtu.be/a'])

    def test_old_videos_are_left_alone_once_measured(self):
        item = video('https://youtu.be/old', 24 * 30)
        self.assertEqual(cf.overview_targets([item], {'https://youtu.be/old': {'snapshots': [{'observed_at': 1}]}}, NOW), [])

    def test_snapshots_keep_their_age_and_are_capped(self):
        store = {}
        item = video('https://youtu.be/a', 26)
        for step in range(15):
            cf.store_overview(store, {'https://youtu.be/a': {'overview_observed_at': NOW + step, 'stayed_to_watch_pct': 50.0 + step}}, [item], NOW)
        snaps = store['https://youtu.be/a']['snapshots']
        self.assertEqual(len(snaps), 12)
        self.assertAlmostEqual(snaps[-1]['age_hours'], 26 + 14 / 3600, places=1)
        self.assertEqual(cf.latest_overview(store, 'https://youtu.be/a')['stayed_to_watch_pct'], 64.0)


class TickWithoutBrowserTests(unittest.TestCase):
    def test_the_core_tick_reads_counters_from_the_api_and_never_touches_the_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            (root / 'data/media_brands.json').write_text(json.dumps({'wrongdev': {'telegram': '@WrongDevp', 'youtube_channel_id': 'UC1'}}))
            api = {'https://youtu.be/aaaaaaaaaaa': {'views': 1249, 'likes': 30, 'comments': 2, 'observed_at': NOW, 'platform': 'youtube'}}
            channels = {'UC1': {'subscribers': 12, 'views': 5000, 'videos': 20, 'observed_at': NOW}}
            with patch.object(cf, 'BASE_DIR', root), patch.object(cf, 'DATA', root / 'data'), \
                    patch.object(cf, 'SERIES', root / 'data/s.jsonl'), patch.object(cf, 'OVERVIEW', root / 'data/o.json'), \
                    patch.object(cf, 'inventory', return_value=[video('https://youtu.be/aaaaaaaaaaa', 30)]), \
                    patch.object(cf, 'telegram_metrics', return_value={}), \
                    patch.object(cf, 'api_metrics', return_value=(api, channels, {'https://youtu.be/aaaaaaaaaaa': {'views': 1249}})), \
                    patch.object(cf, 'run_browser') as browser, patch('time.time', return_value=NOW):
                cf.tick(browser=False)
            browser.assert_not_called()
            feedback = json.loads((root / 'data/content_feedback.json').read_text())
            self.assertEqual(feedback['channels']['UC1']['subscribers'], 12)
            self.assertEqual(feedback['errors'], {})
            self.assertEqual(json.loads((root / 'data/s.jsonl').read_text().splitlines()[0])['views'], 1249)

    def test_an_api_outage_is_reported_and_does_not_zero_the_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            (root / 'data/media_brands.json').write_text(json.dumps({'wrongdev': {}}))
            with patch.object(cf, 'BASE_DIR', root), patch.object(cf, 'DATA', root / 'data'), \
                    patch.object(cf, 'SERIES', root / 'data/s.jsonl'), patch.object(cf, 'OVERVIEW', root / 'data/o.json'), \
                    patch.object(cf, 'inventory', return_value=[video('https://youtu.be/aaaaaaaaaaa', 30)]), \
                    patch.object(cf, 'api_metrics', side_effect=RuntimeError('quota')), patch.object(cf, 'run_browser'), \
                    patch('time.time', return_value=NOW):
                cf.tick(browser=False)
            feedback = json.loads((root / 'data/content_feedback.json').read_text())
            self.assertIn('youtube_api', feedback['errors'])
            latest = json.loads((root / 'output/content_analytics/latest.json').read_text())
            self.assertEqual(latest['items'][0]['metrics'], {})  # unknown, not zero


class ProfileGridTests(unittest.TestCase):
    def test_tiktok_tiles_match_by_video_id_and_empty_tiles_are_skipped(self):
        import metrics_numbers as mn
        items = [{'platform': 'tiktok', 'url': 'https://www.tiktok.com/@u/video/700001'}, {'platform': 'tiktok', 'url': 'https://www.tiktok.com/@u/video/700002'},
            {'platform': 'youtube', 'url': 'https://youtu.be/x'}]
        rows = [{'href': 'https://www.tiktok.com/@u/video/700001', 'text': '1.2K'}, {'href': 'https://www.tiktok.com/@u/video/700002', 'text': ''}]
        out = mn.match_tiles('tiktok', items, rows, NOW)
        self.assertEqual(list(out), ['https://www.tiktok.com/@u/video/700001'])
        self.assertEqual(out['https://www.tiktok.com/@u/video/700001']['views'], 1200)

    def test_instagram_reel_tiles_match_by_shortcode_with_thousands_separators(self):
        import metrics_numbers as mn
        items = [{'platform': 'instagram', 'url': 'https://www.instagram.com/reel/Cx_9-a/'}]
        out = mn.match_tiles('instagram', items, [{'href': 'https://www.instagram.com/reel/Cx_9-a/', 'text': '12,345\nplays'}], NOW)
        self.assertEqual(out['https://www.instagram.com/reel/Cx_9-a/']['views'], 12345)

    def test_the_plural_reels_url_our_publisher_stores_matches_the_singular_grid_link(self):
        import metrics_numbers as mn
        items = [{'platform': 'instagram', 'url': 'https://www.instagram.com/reels/DfMKds4sHny/'}]
        rows = [{'href': 'https://www.instagram.com/vandsaf/reel/DfMKds4sHny/', 'text': '1.8K\n'}]
        self.assertEqual(mn.match_tiles('instagram', items, rows, NOW)['https://www.instagram.com/reels/DfMKds4sHny/']['views'], 1800)


if __name__ == '__main__':unittest.main()
