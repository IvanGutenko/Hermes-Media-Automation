"""Flow thumbnails are transport URLs, not generation identities."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch,Mock,AsyncMock
sys.path.insert(0, '/app/scripts')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import flow_automator as flow


class MediaIdentityTests(unittest.TestCase):
    def setUp(self):
        flow.MEDIA_IDS.clear()
        flow.MEDIA_INFO.clear()

    def test_image_video_and_signed_query_have_one_identity(self):
        self.assertEqual(flow.media_id('https://flow-content.google/image/asset?Expires=1'),
                         flow.media_id('https://flow-content.google/video/asset?Expires=2'))

    def test_restored_gallery_maps_thumbnail_to_real_media_id(self):
        identity = '12345678-1234-1234-1234-123456789abc'
        project = flow.PROJECT_URL.rsplit('/', 1)[-1]
        record = [identity, project, 'generation-id', None, None,
                  [[1234, 0], 'exact submitted prompt', 'https://lh3.googleusercontent.com/asb/opaque-token=s512-rw'],
                  None, []]
        payload = json.dumps([None, [], [record]])
        response = ")]}'\n\n100\n" + json.dumps([['wrb.fr', 'Zzl0ze', payload]])
        flow.learn_media_metadata(response)
        self.assertEqual(flow.media_id('https://flow.google.com/asb/opaque-token=mm,22,15'), '/image/' + identity)
        self.assertEqual(flow.MEDIA_INFO['/image/' + identity]['prompt'], 'exact submitted prompt')
        self.assertEqual(flow.MEDIA_INFO['/image/' + identity]['created_at'], 1234)

    def test_unknown_thumbnail_is_rejected_instead_of_becoming_a_new_asset(self):
        with self.assertRaises(RuntimeError):
            flow.media_id('https://flow.google.com/asb/unknown-thumbnail=s512-rw')


class MetadataDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_credit_warning_image_is_a_terminal_quota_refusal_before_submission(self):
        page=Mock();page.locator.return_value.is_visible=AsyncMock(return_value=False)
        def control(role,**kw):
            result=Mock();result.is_visible=AsyncMock(return_value=kw.get('name')=='Insufficient credits warning')
            return result
        page.get_by_role.side_effect=control
        with self.assertRaises(flow.ProviderUnavailable) as error:
            await flow.configure_video(page)
        self.assertEqual(error.exception.kind,'quota_exhausted')
        page.keyboard.press.assert_not_called()

    async def test_entry_wait_uses_visible_controls_without_evaluating_script(self):
        page=Mock();page.url='https://flow.google.com/u/1/'
        page.get_by_role.return_value.is_visible=AsyncMock(return_value=True)
        await flow.wait_flow_entry(page,1)
        page.wait_for_function.assert_not_called()

    async def test_only_the_new_terminal_provider_error_allows_fallback(self):
        page=Mock();page.wait_for_timeout=AsyncMock()
        page.locator.return_value.all_inner_texts=AsyncMock(return_value=['Generation timed out. Please try again.'])
        with patch.object(flow,'video_tiles',new=AsyncMock(return_value=[])):
            with self.assertRaises(flow.ProviderUnavailable) as error:
                await flow.wait_for_new_tile(page,set(),1,baseline_errors=[])
        self.assertEqual(error.exception.kind,'generation_failed')

    def _gallery(self, specs):
        """specs: (key, prompt, created_at) for each tile; the first one is already in the baseline."""
        tiles = []
        for key, prompt, created_at in specs:
            tile = Mock()
            tile.locator.return_value.count = AsyncMock(return_value=0)
            flow.MEDIA_INFO[key] = {'created_at': created_at, 'prompt': prompt}
            tiles.append((key, tile))
        return tiles

    async def _wait(self, tiles, **kwargs):
        page = Mock(); page.wait_for_timeout = AsyncMock()
        with patch.object(flow, 'video_tiles', new=AsyncMock(return_value=tiles)):
            return await flow.wait_for_new_tile(page, {'/image/old'}, 1, baseline_errors=[], **kwargs)

    async def test_retained_request_is_found_among_tiles_another_brand_added_later(self):
        tiles = self._gallery([('/image/old', 'old', 10), ('/image/mine', 'my  exact\nprompt', 2000),
                               ('/image/other-brand', 'another prompt', 3000)])
        key, tile = await self._wait(tiles, submitted_prompt='my exact prompt', submitted_at=1999)
        self.assertEqual(key, '/image/mine')
        self.assertIs(tile, tiles[1][1])

    async def test_two_tiles_with_the_same_prompt_remain_ambiguous(self):
        tiles = self._gallery([('/image/old', 'old', 10), ('/image/a', 'my prompt', 2000), ('/image/b', 'my prompt', 2100)])
        with self.assertRaisesRegex(RuntimeError, 'Multiple new media assets'):
            await self._wait(tiles, submitted_prompt='my prompt', submitted_at=1999)

    async def test_no_tile_with_this_prompt_is_not_guessed(self):
        tiles = self._gallery([('/image/old', 'old', 10), ('/image/a', 'first', 2000), ('/image/b', 'second', 2100)])
        with self.assertRaisesRegex(RuntimeError, 'Multiple new media assets'):
            await self._wait(tiles, submitted_prompt='my prompt', submitted_at=1999)

    async def test_same_prompt_created_before_the_submission_is_not_this_request(self):
        tiles = self._gallery([('/image/old', 'old', 10), ('/image/earlier', 'my prompt', 1000), ('/image/other', 'x', 2100)])
        with self.assertRaisesRegex(RuntimeError, 'Multiple new media assets'):
            await self._wait(tiles, submitted_prompt='my prompt', submitted_at=1999)

    async def test_tile_without_recorded_metadata_cannot_match(self):
        tiles = self._gallery([('/image/old', 'old', 10), ('/image/a', 'my prompt', 2000), ('/image/b', 'x', 2100)])
        del flow.MEDIA_INFO['/image/a']
        with self.assertRaisesRegex(RuntimeError, 'Multiple new media assets'):
            await self._wait(tiles, submitted_prompt='my prompt', submitted_at=1999)

    async def test_without_a_submitted_request_several_new_tiles_still_fail(self):
        tiles = self._gallery([('/image/old', 'old', 10), ('/image/a', 'my prompt', 2000), ('/image/b', 'x', 2100)])
        with self.assertRaisesRegex(RuntimeError, 'Multiple new media assets'):
            await self._wait(tiles)

    async def test_unfinished_background_response_cannot_hold_the_browser_lease(self):
        cancelled=asyncio.Event()
        class Response:
            url='https://flow.google.com/data/batchexecute'
            def __init__(self,slow):self.slow=slow
            async def text(self):
                if not self.slow:return 'metadata'
                try:await asyncio.Event().wait()
                finally:cancelled.set()
        class Page:
            def on(self,event,listener):self.listener=listener
            def remove_listener(self,event,listener):pass
            async def reload(self,**kwargs):
                self.listener(Response(False));self.listener(Response(True))
                await asyncio.sleep(0)
        with patch.dict(flow.MEDIA_IDS,{},clear=True),patch.object(flow,'learn_media_metadata',side_effect=lambda body:flow.MEDIA_IDS.update({'asset':'id'})):
            await asyncio.wait_for(flow.refresh_media_metadata(Page()),timeout=1)
        self.assertTrue(cancelled.is_set())

    async def test_new_clip_is_identified_by_reloading_in_the_same_pass(self):
        tile = Mock(); tile.locator.return_value.count = AsyncMock(return_value=0)
        flow.MEDIA_INFO['/image/mine'] = {'created_at': 2000, 'prompt': 'my prompt'}
        page = Mock(); page.wait_for_timeout = AsyncMock()
        tiles = AsyncMock(side_effect=[flow.UnknownMediaIdentity('unknown'), [('/image/mine', tile)]])
        refresh = AsyncMock()
        with patch.object(flow, 'video_tiles', new=tiles), patch.object(flow, 'refresh_media_metadata', new=refresh):
            key, found = await flow.wait_for_new_tile(page, set(), 1, baseline_errors=[], submitted_prompt='my prompt', submitted_at=1999)
        self.assertEqual(key, '/image/mine')
        self.assertIs(found, tile)
        refresh.assert_awaited_once_with(page)

    async def test_identity_that_never_arrives_is_not_treated_as_a_new_clip(self):
        page = Mock(); page.wait_for_timeout = AsyncMock()
        page.locator.return_value.all_inner_texts = AsyncMock(return_value=[])
        tiles = AsyncMock(side_effect=flow.UnknownMediaIdentity('unknown'))
        with patch.object(flow, 'video_tiles', new=tiles), patch.object(flow, 'refresh_media_metadata', new=AsyncMock()), \
                self.assertRaises(TimeoutError):
            await flow.wait_for_new_tile(page, set(), 0.05, baseline_errors=[])

    async def test_a_single_new_tile_of_another_request_is_not_adopted(self):
        tiles = self._gallery([('/image/old', 'old', 10), ('/image/other-brand', 'another prompt', 3000)])
        page = Mock(); page.wait_for_timeout = AsyncMock()
        page.locator.return_value.all_inner_texts = AsyncMock(return_value=[])
        with patch.object(flow, 'video_tiles', new=AsyncMock(return_value=tiles)), self.assertRaises(TimeoutError):
            await flow.wait_for_new_tile(page, {'/image/old'}, 0.05, baseline_errors=[], submitted_prompt='my prompt', submitted_at=1999)

    async def test_a_single_new_tile_without_recorded_metadata_keeps_working(self):
        tile = Mock(); tile.locator.return_value.count = AsyncMock(return_value=0)
        page = Mock(); page.wait_for_timeout = AsyncMock()
        with patch.object(flow, 'video_tiles', new=AsyncMock(return_value=[('/image/new', tile)])):
            key, _ = await flow.wait_for_new_tile(page, set(), 1, baseline_errors=[], submitted_prompt='my prompt', submitted_at=1999)
        self.assertEqual(key, '/image/new')

    def test_lost_request_needs_a_loaded_list_old_age_and_no_record(self):
        submitted = 10000
        flow.MEDIA_INFO['/image/other'] = {'created_at': 20000, 'prompt': 'another prompt'}
        self.assertFalse(flow.request_is_lost('my prompt', submitted, now=submitted + flow.REQUEST_LOST_SECONDS - 1))
        self.assertTrue(flow.request_is_lost('my prompt', submitted, now=submitted + flow.REQUEST_LOST_SECONDS))
        flow.MEDIA_INFO['/image/mine'] = {'created_at': submitted + 1, 'prompt': 'my   prompt'}
        self.assertFalse(flow.request_is_lost('my prompt', submitted, now=submitted + 99999))
        flow.MEDIA_INFO.clear()
        self.assertFalse(flow.request_is_lost('my prompt', submitted, now=submitted + 99999))

    async def test_a_hanging_editor_cannot_undo_a_finished_clip(self):
        page = Mock()
        done = page.get_by_role.return_value
        done.is_visible = AsyncMock(return_value=True)
        done.click = AsyncMock(side_effect=TimeoutError('waiting for scheduled navigations to finish'))
        await flow.leave_editor(page)
        done.click.assert_awaited_once_with(no_wait_after=True)

    def _metadata_page(self, batches):
        """Each reload delivers its list of bodies through the page's response listener."""
        class Response:
            url = 'https://flow.google.com/data/batchexecute'
            def __init__(self, body): self.body = body
            async def text(self): return self.body
        class Page:
            reloads = 0
            def on(self, event, listener): self.listener = listener
            def remove_listener(self, event, listener): pass
            async def reload(self, **kwargs):
                bodies = batches[self.reloads]; self.reloads += 1
                for body in bodies: self.listener(Response(body))
                await asyncio.sleep(0)
        return Page()

    @staticmethod
    def _teach(body):
        if body != 'unrelated': flow.MEDIA_IDS[body] = '/image/' + body

    async def test_an_unrelated_response_does_not_end_a_later_refresh(self):
        flow.MEDIA_IDS['/asb/old'] = '/image/old'
        page = self._metadata_page([['unrelated'], ['/asb/new']])
        with patch.object(flow, 'learn_media_metadata', side_effect=self._teach), patch.object(flow, 'METADATA_WAIT_SECONDS', 0.05):
            await flow.refresh_media_metadata(page)
        self.assertEqual(page.reloads, 2)
        self.assertEqual(flow.MEDIA_IDS, {'/asb/new': '/image//asb/new'})

    async def test_project_list_lost_in_every_reload_still_fails(self):
        page = self._metadata_page([['unrelated']] * flow.METADATA_RELOADS)
        with patch.object(flow, 'learn_media_metadata', side_effect=self._teach), patch.object(flow, 'METADATA_WAIT_SECONDS', 0.02):
            with self.assertRaisesRegex(RuntimeError, 'metadata was not received'):
                await flow.refresh_media_metadata(page)
        self.assertEqual(page.reloads, flow.METADATA_RELOADS)


if __name__ == '__main__':
    unittest.main()
