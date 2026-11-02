"""Chrome on gut-server-2 shares 4.9 GB and a spinning disk with everything else: optional browser work must
wait for calm, rotate between sites, and never leave spare tabs behind."""
import asyncio
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import content_feedback as cf
import host_load
import media_health as health

NOW = 1_800_000_000
CALM = {'available_mib': 2200, 'memory_pressure': 1.0, 'io_pressure': 5.0}


class HostLoadTests(unittest.TestCase):
    def test_calm_host_is_not_busy_and_unknown_readings_never_block(self):
        self.assertIsNone(host_load.busy(CALM))
        self.assertIsNone(host_load.busy({'available_mib': None, 'memory_pressure': None, 'io_pressure': None}))

    def test_each_kind_of_shortage_is_reported_with_its_numbers(self):
        self.assertIn('900 MiB', host_load.busy({**CALM, 'available_mib': 900}))
        self.assertIn('memory pressure 40.0%', host_load.busy({**CALM, 'memory_pressure': 40.0}))
        self.assertIn('disk I/O pressure 90.0%', host_load.busy({**CALM, 'io_pressure': 90.0}))

    def test_the_measured_idle_range_of_this_host_is_calm(self):
        # Idle readings on 2026-11-01: disk pressure 3-35 %, stalls 85-98 %.
        self.assertIsNone(host_load.busy({**CALM, 'io_pressure': 35.0}))
        self.assertIsNotNone(host_load.busy({**CALM, 'io_pressure': 85.0}))

    def test_wait_calm_returns_when_pressure_drops_and_gives_up_at_the_deadline(self):
        readings = iter([{**CALM, 'io_pressure': 95.0}, {**CALM, 'io_pressure': 80.0}, CALM])
        now = [0.0]
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            now[0] += seconds
        self.assertIsNone(host_load.wait_calm(300, poll=15, read=lambda: next(readings), sleep=sleep, clock=lambda: now[0]))
        self.assertEqual(slept, [15, 15])
        now[0] = 0.0
        busy = host_load.wait_calm(60, poll=30, read=lambda: {**CALM, 'io_pressure': 99.0}, sleep=sleep, clock=lambda: now[0])
        self.assertIn('disk I/O pressure', busy)
        self.assertGreaterEqual(now[0], 60)


def item(platform, url='https://x/' + 'a'):
    return {'brand': 'wrongdev', 'platform': platform, 'kind': 'post', 'url': url + platform, 'published_at': NOW - 100}


class RotationTests(unittest.TestCase):
    ITEMS = [item(p) for p in ('telegram', 'youtube', 'vk', 'dzen', 'reddit', 'tiktok', 'instagram')]

    def test_only_two_platforms_with_published_items_are_read_per_pass_oldest_first(self):
        state = {'vk': {'tried': NOW - 5 * 3600, 'ok': True}, 'dzen': {'tried': NOW - 9 * 3600, 'ok': True},
                 'reddit': {'tried': NOW - 7 * 3600, 'ok': True}, 'tiktok': {'tried': NOW - 8 * 3600, 'ok': True},
                 'instagram': {'tried': NOW - 7 * 3600, 'ok': True}}
        self.assertEqual(cf.due_platforms(self.ITEMS, state, NOW), ['dzen', 'tiktok'])

    def test_a_platform_read_recently_waits_for_its_interval(self):
        state = {'vk': {'tried': NOW - 2 * 3600, 'ok': True}, 'dzen': {'tried': NOW - 5 * 3600, 'ok': True}}
        due = cf.due_platforms(self.ITEMS, state, NOW, limit=9)
        self.assertNotIn('vk', due)
        self.assertNotIn('dzen', due)
        self.assertEqual(set(due), {'reddit', 'tiktok', 'instagram'})

    def test_a_failed_reading_is_retried_after_an_hour_not_at_the_next_pass_and_not_after_six(self):
        failed = {'instagram': {'tried': NOW - 1800, 'ok': False}}
        self.assertNotIn('instagram', cf.due_platforms(self.ITEMS, failed, NOW, limit=9))
        failed['instagram']['tried'] = NOW - 3700
        self.assertIn('instagram', cf.due_platforms(self.ITEMS, failed, NOW, limit=9))

    def test_a_platform_without_published_items_is_never_opened(self):
        self.assertEqual(cf.due_platforms([item('telegram')], {}, NOW), [])
        self.assertEqual(cf.due_platforms([item('reddit'), item('dzen')], {}, NOW, limit=9), ['dzen', 'reddit'])

    def test_readings_of_waiting_platforms_stay_visible_but_marked_stale_until_two_intervals_pass(self):
        previous = [
            {'url': 'u1', 'platform': 'dzen', 'metrics': {'views': 5, 'observed_at': NOW - 10 * 3600}},
            {'url': 'u2', 'platform': 'dzen', 'metrics': {'views': 6, 'observed_at': NOW - 13 * 3600}},
            {'url': 'u3', 'platform': 'telegram', 'metrics': {'views': 7, 'observed_at': NOW - 1800}},
            {'url': 'u4', 'platform': 'vk', 'metrics': {}},
        ]
        metrics = {}
        cf.carry_over(previous, metrics, NOW, only=tuple(cf.PLATFORM_INTERVAL_HOURS))
        self.assertEqual(metrics, {'u1': {'views': 5, 'observed_at': NOW - 10 * 3600, 'stale': True}})
        both = {}
        cf.carry_over(previous, both, NOW)
        self.assertEqual(set(both), {'u1', 'u3'})   # telegram keeps the 4 h rule of the 15-minute ticks
        fresh = {'u1': {'views': 9}}
        cf.carry_over(previous, fresh, NOW, only=tuple(cf.PLATFORM_INTERVAL_HOURS))
        self.assertEqual(fresh['u1'], {'views': 9})  # a new reading is never replaced


class PassGateTests(unittest.TestCase):
    def collect(self, items, state=None, overview=None, calm=None, run_result=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            state_file = root / 'data/browser_pass_state.json'
            if state is not None:
                state_file.write_text(json.dumps(state))
            run = Mock(return_value=run_result or Mock(returncode=0, stdout=json.dumps(
                {'metrics': {}, 'errors': {'instagram': 'Timeout'}, 'ran': ['dzen', 'instagram'], 'skipped': []}) + '\n', stderr=''))
            with patch.object(cf, 'BASE_DIR', root), patch.object(cf, 'PASS_STATE', state_file), patch.object(cf, 'run_browser', run), \
                    patch.object(host_load, 'wait_calm', return_value=calm) as wait, patch.object(cf.time, 'time', return_value=NOW):
                result = cf.collect_browser({}, items, overview or {}, NOW)
            written = json.loads(state_file.read_text()) if state_file.exists() else {}
            job = json.loads((root / 'output/content_analytics/browser_job.json').read_text()) if (root / 'output/content_analytics/browser_job.json').exists() else None
        return result, run, wait, written, job

    def test_nothing_due_leaves_the_browser_alone(self):
        fresh = {p: {'tried': NOW - 60, 'ok': True} for p in cf.PLATFORM_INTERVAL_HOURS}
        (observed, errors), run, wait, _, job = self.collect(RotationTests.ITEMS, fresh)
        self.assertEqual((observed, errors), ({}, {}))
        run.assert_not_called()
        wait.assert_not_called()
        self.assertIsNone(job)

    def test_a_busy_host_postpones_the_reading_without_reporting_an_error(self):
        (observed, errors), run, _, written, job = self.collect(RotationTests.ITEMS, {'last_pass_at': NOW - 3600}, calm='disk I/O pressure 90.0% over the last minute')
        self.assertEqual((observed, errors), ({}, {}))
        run.assert_not_called()
        self.assertIsNone(job)
        self.assertIn('disk I/O', written['skipped']['reason'])
        self.assertEqual(written['skipped']['due'], ['dzen', 'instagram'])

    def test_a_host_that_never_calms_down_is_still_read_after_six_hours(self):
        (observed, _), run, wait, written, job = self.collect(RotationTests.ITEMS, {'last_pass_at': NOW - 7 * 3600}, calm='busy')
        run.assert_called_once()
        wait.assert_not_called()
        self.assertTrue(job['force'])
        self.assertEqual(job['platforms'], ['dzen', 'instagram'])   # nothing read yet: ties go alphabetically

    def test_a_calm_pass_reads_two_platforms_and_records_who_succeeded(self):
        (observed, errors), run, _, written, job = self.collect(RotationTests.ITEMS, {'last_pass_at': NOW - 3600, 'vk': {'tried': NOW - 3600, 'ok': True}})
        run.assert_called_once()
        self.assertEqual(len(job['platforms']), 2)
        self.assertFalse(job['force'])
        self.assertEqual(written['dzen'], {'tried': NOW, 'ok': True})
        self.assertEqual(written['instagram'], {'tried': NOW, 'ok': False})
        self.assertEqual(written['last_pass_at'], NOW)
        self.assertNotIn('skipped', written)


class FakePage:
    def __init__(self, url, log):
        self.url, self.log = url, log

    async def close(self):
        self.log.append(('close', self.url))

    async def goto(self, url, **_):
        self.log.append(('goto', self.url, url))
        self.url = url


def run_tidy(urls, vnc_clients=0):
    """Executes the exact script media_health sends into the browser container against fake pages."""
    log = []
    pages = [FakePage(u, log) for u in urls]
    browser = types.SimpleNamespace(contexts=[types.SimpleNamespace(pages=pages)])

    async def close():
        return None
    browser.close = close

    async def connect_over_cdp(*_, **__):
        return browser

    class Playwright:
        chromium = types.SimpleNamespace(connect_over_cdp=connect_over_cdp)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False
    api = types.ModuleType('playwright.async_api')
    api.async_playwright = Playwright
    table = '  sl  local_address rem_address   st\n' + ''.join(
        f'   {i}: 0100007F:170C 0100007F:9C4{i} 01 00000000:00000000\n' for i in range(vnc_clients))
    table += '   9: 0100007F:2406 0100007F:9D00 01 00000000:00000000\n'   # DevTools connection: not the desktop

    def fake_open(name, *a, **k):
        if name == '/proc/net/tcp':
            return io.StringIO(table)
        raise FileNotFoundError(name)
    out = io.StringIO()
    captured = {}

    def fake_run(argv, **_):
        captured['code'] = argv[-1]
        return Mock(returncode=0, stdout='')
    with patch('browser_process.run', side_effect=fake_run):
        health.park_idle_browser()
    with patch.dict(sys.modules, {'playwright': types.ModuleType('playwright'), 'playwright.async_api': api}), \
            patch('sys.stdout', out):
        exec(compile(captured['code'], '<container script>', 'exec'), {'__name__': '__main__', 'open': fake_open})
    return log, int(out.getvalue().strip().splitlines()[-1])


FLOW_PROJECT = 'https://flow.google.com/u/0/project/23388e4b'
FLOW_EDIT = FLOW_PROJECT + '/edit/9474db3b'
ACCOUNT = 'https://myaccount.google.com/'


class TidyTests(unittest.TestCase):
    def test_spare_editor_and_duplicate_account_tabs_are_closed_and_the_project_page_is_kept(self):
        log, count = run_tidy([FLOW_PROJECT, FLOW_EDIT, ACCOUNT, ACCOUNT])
        self.assertEqual(sorted(log), sorted([('close', FLOW_EDIT), ('close', ACCOUNT)]))
        self.assertEqual(count, 2)

    def test_an_editor_tab_is_kept_when_no_project_page_exists_to_take_over(self):
        log, count = run_tidy([FLOW_EDIT, ACCOUNT])
        self.assertEqual(log, [])
        self.assertEqual(count, 0)

    def test_instagram_and_tiktok_are_parked_and_the_flow_landing_page_too(self):
        log, count = run_tidy([ACCOUNT, 'https://www.instagram.com/reels/abc/', 'https://www.tiktok.com/@x', 'https://flow.google.com/u/0/'])
        self.assertEqual([entry[:2] for entry in log], [('goto', 'https://www.instagram.com/reels/abc/'), ('goto', 'https://www.tiktok.com/@x'), ('goto', 'https://flow.google.com/u/0/')])
        self.assertEqual(count, 3)

    def test_the_last_window_is_never_closed(self):
        log, _ = run_tidy([ACCOUNT, ACCOUNT])
        self.assertEqual(log, [('close', ACCOUNT)])

    def test_nothing_moves_while_the_owner_is_connected_to_the_desktop(self):
        log, count = run_tidy([FLOW_PROJECT, FLOW_EDIT, ACCOUNT, ACCOUNT, 'https://www.instagram.com/accounts/login/'], vnc_clients=1)
        self.assertEqual(log, [])
        self.assertEqual(count, 0)

    def test_other_pages_such_as_studio_and_login_forms_are_left_alone(self):
        log, _ = run_tidy([ACCOUNT, 'https://studio.youtube.com/channel/UC1', 'https://www.youtube.com/channel_switcher', 'https://vk.ru/club1'])
        self.assertEqual(log, [])


if __name__ == '__main__':
    unittest.main()
