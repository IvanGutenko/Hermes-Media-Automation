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
    async def test_only_the_new_terminal_provider_error_allows_fallback(self):
        page=Mock();page.wait_for_timeout=AsyncMock()
        page.locator.return_value.all_inner_texts=AsyncMock(return_value=['Generation timed out. Please try again.'])
        with patch.object(flow,'video_tiles',new=AsyncMock(return_value=[])):
            with self.assertRaises(flow.ProviderUnavailable) as error:
                await flow.wait_for_new_tile(page,set(),1,baseline_errors=[])
        self.assertEqual(error.exception.kind,'generation_failed')

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


if __name__ == '__main__':
    unittest.main()
