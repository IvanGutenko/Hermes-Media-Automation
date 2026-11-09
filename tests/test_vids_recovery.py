"""A restored, empty Vids canvas must release the shared browser without resubmitting."""
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'runs in the browser container')
class VidsRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_reloaded_canvas_retains_identity_without_generating_again(self):
        import vids_automator as v
        prompt = 'An English visual prompt long enough for this saved scene and its exact identity.'
        state = {'run_id': 'saved', 'account': 1, 'status': 'error', 'prompt': prompt,
            'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
            'document_url': 'https://docs.google.com/videos/d/saved/edit',
            'submitted_at': 123, 'submission_attempted_at': 122}
        manager = MagicMock()
        manager.__aenter__ = AsyncMock()
        manager.__aexit__ = AsyncMock()
        browser = MagicMock(); browser.close = AsyncMock()
        context = browser.contexts[0]; context.pages = []
        page = MagicMock(); page.goto = AsyncMock(); page.wait_for_url = AsyncMock()
        page.url = state['document_url']
        page.close = AsyncMock(); context.new_page = AsyncMock(return_value=page)
        locator = MagicMock(); locator.count = AsyncMock(return_value=0)
        locator.wait_for = AsyncMock(); locator.click = AsyncMock(side_effect=TimeoutError('panel missing'))
        page.get_by_role.return_value = locator
        with tempfile.TemporaryDirectory() as folder, patch.object(v, 'OUTPUT_DIR', Path(folder)), \
                patch.object(v, 'async_playwright', return_value=manager), \
                patch.object(v, 'connect', new=AsyncMock(return_value=browser)), \
                patch.object(v, 'set_frozen', new=AsyncMock()) as freeze, \
                patch.object(v, 'hide_heavy_tabs', new=AsyncMock()):
            (Path(folder)/'vids_saved.json').write_text(json.dumps(state))
            result = await v.generate_vids_video(prompt, run_id='saved', timeout_seconds=0)
            self.assertIn('original request retained', result['error'])
            self.assertEqual(result['submitted_at'], 123)
            self.assertEqual(result['document_url'], state['document_url'])
            self.assertNotIn('terminal_failure_observed', result)
            page.goto.assert_awaited_once_with(state['document_url'], wait_until='commit', timeout=60000)
            freeze.assert_awaited_once_with(page, True)
            page.close.assert_not_awaited()
            browser.close.assert_awaited_once()
            # Only the sidebar reopen was attempted; never the generation button.
            self.assertEqual(locator.click.await_count, 1)
            self.assertEqual(locator.click.await_args.kwargs, {'timeout': 20000})
