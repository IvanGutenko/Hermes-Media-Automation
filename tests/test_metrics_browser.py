"""The hourly signed-in browser pass: light pages, one tab at a time, nothing when the host is short of memory.
Needs playwright, which lives in the browser container only."""
import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
try:
    import media_metrics_browser as mm
except ImportError:
    mm = None

CALM = {'available_mib': 2200, 'memory_pressure': 1.0, 'io_pressure': 5.0}


class Session:
    def __init__(self, log):
        self.log = log

    async def send(self, method, params=None):
        self.log.append((method, params))


class Page:
    def __init__(self, log):
        self.log = log
        self.context = types.SimpleNamespace(new_cdp_session=self.new_session)

    async def new_session(self, page):
        return Session(self.log)

    def set_default_timeout(self, _):
        pass

    async def close(self):
        self.log.append(('close',))


@unittest.skipIf(mm is None, 'needs the browser container')
class PassTests(unittest.TestCase):
    def run_pass(self, reading, platforms=('vk', 'dzen', 'tiktok'), job_extra=None, readers=None):
        log, opened = [], []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = {'items': [], 'brands': {}, 'overview_targets': [], 'platforms': list(platforms), **(job_extra or {})}
            (root / 'job.json').write_text(json.dumps(job))

            class Browser:
                contexts = [types.SimpleNamespace(new_page=None)]

                async def close(self):
                    pass

            async def new_page():
                opened.append(1)
                return Page(log)
            Browser.contexts[0].new_page = new_page

            async def connect(_):
                log.append(('connect',))
                return Browser()

            class Playwright:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_):
                    return False

            def reader(name):
                async def read(page, items, metrics, raw):
                    log.append(('read', name))
                return read
            with patch.object(mm, 'ROOT', root), patch.object(mm, 'connect', connect), patch.object(mm, 'async_playwright', Playwright), \
                    patch.object(mm.host_load, 'reading', return_value=reading), \
                    patch.multiple(mm, **{**{n: reader(n) for n in mm.ORDER}, **(readers or {})}):
                result = asyncio.run(mm.collect(str(root / 'job.json')))
        return result, log, opened

    def test_pages_block_images_video_and_fonts_through_url_patterns_not_routing(self):
        result, log, _ = self.run_pass(CALM, platforms=('vk',))
        method, params = next(entry for entry in log if entry[0] == 'Network.setBlockedURLs')
        for pattern in ('*.jpg*', '*.png*', '*.webp*', '*.mp4*', '*.woff*'):
            self.assertIn(pattern, params['urls'])
        self.assertNotIn(('route',), log)
        self.assertEqual(result['ran'], ['vk'])

    def test_platforms_are_read_in_the_fixed_order_and_only_the_requested_ones(self):
        result, log, opened = self.run_pass(CALM, platforms=('tiktok', 'reddit', 'vk'))
        self.assertEqual([e[1] for e in log if e[0] == 'read'], ['vk', 'tiktok'])
        self.assertEqual(len(opened), 2)
        self.assertEqual(result['skipped'], [])

    def test_a_stale_reddit_only_job_does_not_connect_to_the_browser(self):
        result,log,opened=self.run_pass(CALM,platforms=('reddit',))
        self.assertNotIn(('connect',),log)
        self.assertEqual(opened,[])
        self.assertEqual(result['ran'],[])

    def test_a_busy_host_opens_no_tab_and_the_platforms_wait_for_the_next_pass(self):
        result, log, opened = self.run_pass({**CALM, 'io_pressure': 92.0})
        self.assertEqual(opened, [])
        self.assertEqual(result['ran'], [])
        self.assertEqual(len(result['skipped']), 3)
        self.assertIn('disk I/O pressure', result['skipped'][0])
        self.assertEqual(result['errors'], {})

    def test_a_forced_pass_ignores_the_load_check(self):
        result, _, opened = self.run_pass({**CALM, 'io_pressure': 92.0}, platforms=('vk',), job_extra={'force': True})
        self.assertEqual(len(opened), 1)
        self.assertEqual(result['ran'], ['vk'])

    def test_tiktok_waits_for_the_counters_to_be_drawn_before_reading_the_grid(self):
        order = []

        class Tiles:
            first = types.SimpleNamespace(wait_for=None)

            async def evaluate_all(self, script):
                order.append('read')
                return [{'href': 'https://www.tiktok.com/@u/video/1', 'text': '12'}]

        async def appear(timeout):
            order.append('tile')
        Tiles.first.wait_for = appear
        tiles = Tiles()

        class TikTokPage:
            def locator(self, selector):
                return tiles

            async def goto(self, url, **kwargs):
                order.append('goto')

            async def wait_for_function(self, script, timeout):
                order.append('counters')
                assert 'video-views' in script
        items = [{'platform': 'tiktok', 'url': 'https://www.tiktok.com/@u/video/1', 'brand': 'wrongdev'}]
        with patch.dict(mm.ACCOUNTS, {'tiktok': {'u'}}, clear=True):
            asyncio.run(mm.tiktok(TikTokPage(), items, {}, {}))
        self.assertEqual(order, ['goto', 'tile', 'counters', 'read'])

    def test_a_reader_failure_is_recorded_and_the_tab_is_still_closed(self):
        async def broken(page, items, metrics, raw):
            raise RuntimeError('no tiles')
        result, log, _ = self.run_pass(CALM, platforms=('vk',), readers={'vk': broken})
        self.assertIn('no tiles', result['errors']['vk'])
        self.assertIn(('close',), log)
        self.assertEqual(result['ran'], ['vk'])


if __name__ == '__main__':
    unittest.main()
