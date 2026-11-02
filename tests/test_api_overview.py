"""Per-video Analytics API readings replace Studio page loads for videos old enough to have API data."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import content_feedback as cf

NOW = 1_800_000_000
CONFIGS = {'wrongdev': {'youtube_channel_id': 'UC1'}, 'migratoria': {'youtube_channel_id': 'UC2'}}


def video(code, hours, brand='wrongdev'):
    return {'brand': brand, 'platform': 'youtube', 'kind': 'video', 'url': 'https://youtu.be/' + code * 11, 'published_at': NOW - hours * 3600}


class ApiOverviewTests(unittest.TestCase):
    def run_api(self, items, store=None, have=('UC1',), fetch=None):
        calls = []

        def default(channel, video_id, published_at, now):
            calls.append((channel, video_id))
            return {'views': 100, 'overview_observed_at': now, 'average_view_duration_s': 12}
        store = {} if store is None else store
        values, errors = cf.api_overview(items, CONFIGS, store, NOW, fetch=fetch or default, have=have)
        return values, errors, calls, store

    def test_only_channels_with_a_stored_consent_and_videos_past_the_data_lag_are_asked(self):
        items = [video('a', 80), video('b', 30), video('c', 100, 'migratoria')]
        values, _, calls, _ = self.run_api(items)
        self.assertEqual(calls, [('UC1', 'a' * 11)])
        self.assertEqual(list(values), [items[0]['url']])

    def test_without_any_consent_nothing_is_called(self):
        values, errors, calls, store = self.run_api([video('a', 80)], have=())
        self.assertEqual((values, errors, calls, store), ({}, {}, [], {}))

    def test_an_attempt_is_not_repeated_inside_its_interval_even_when_the_answer_was_empty(self):
        item = video('a', 80)
        _, _, calls, store = self.run_api([item], fetch=lambda *a: {})
        self.assertEqual(store[item['url']]['api_attempt'], NOW)
        _, _, calls, _ = self.run_api([item], store=store)
        self.assertEqual(calls, [])
        store[item['url']]['api_attempt'] = NOW - 25 * 3600
        _, _, calls, _ = self.run_api([item], store=store)
        self.assertEqual(len(calls), 1)

    def test_a_week_old_video_is_asked_every_three_days_not_daily(self):
        item = video('a', 24 * 8)
        store = {item['url']: {'snapshots': [], 'api_attempt': NOW - 30 * 3600}}
        _, _, calls, _ = self.run_api([item], store=store)
        self.assertEqual(calls, [])

    def test_a_refused_call_stops_the_tick_and_is_reported_once(self):
        def refuse(*a):
            raise RuntimeError('HTTP 403: quota')
        items = [video(c, 80) for c in 'abc']
        values, errors, _, store = self.run_api(items, fetch=refuse)
        self.assertEqual(values, {})
        self.assertEqual(list(errors), ['yt_analytics'])
        self.assertIn('403', errors['yt_analytics'])
        self.assertEqual(sum(1 for e in store.values() if e.get('api_attempt')), 1)

    def test_at_most_ten_videos_per_tick_never_asked_ones_first(self):
        items = [video(chr(97 + i), 80 + i) for i in range(14)]
        store = {items[3]['url']: {'snapshots': [], 'api_attempt': NOW - 90 * 3600}}
        _, _, calls, _ = self.run_api(items, store=store)
        self.assertEqual(len(calls), cf.API_PER_TICK)
        self.assertNotIn(('UC1', 'd' * 11), calls)   # asked before, so it waits behind videos never asked

    def test_studio_is_skipped_for_videos_the_api_covers_and_still_used_for_young_ones(self):
        old, young = video('a', 100), video('b', 30)
        covered = {x['url'] for x in cf.api_videos([old, young], CONFIGS, NOW, {'wrongdev'})}
        self.assertEqual(cf.overview_targets([old, young], {}, NOW, skip=covered), [young['url']])
        self.assertEqual(set(cf.overview_targets([old, young], {}, NOW)), {old['url'], young['url']})

    def test_the_api_snapshot_of_an_old_video_does_not_hide_studio_stayed_to_watch(self):
        store = {'u': {'snapshots': [
            {'observed_at': 1, 'stayed_to_watch_pct': 61.0, 'average_view_duration_s': 18, 'traffic_sources': {'Shorts feed': 90.0}},
            {'observed_at': 2, 'average_view_duration_s': 16, 'engaged_views_pct': 70.0, 'traffic_sources': {'Shorts feed': 80.0}}]}}
        self.assertEqual(cf.latest_overview(store, 'u'), {'stayed_to_watch_pct': 61.0, 'average_view_duration_s': 16,
                                                         'engaged_views_pct': 70.0, 'traffic_sources': {'Shorts feed': 80.0}})
        self.assertEqual(cf.latest_overview(store, 'missing'), {})


if __name__ == '__main__':
    unittest.main()
