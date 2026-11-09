"""A saved upload whose dialog was closed is reopened, never uploaded again."""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,MagicMock,patch
sys.path.insert(0,'/app/scripts')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))


@unittest.skipUnless(importlib.util.find_spec('playwright'),'runs in the browser container')
class ReopenDraftTests(unittest.IsolatedAsyncioTestCase):
    def test_english_draft_with_verified_language_resumes_visibility(self):
        import youtube_studio_uploader as u
        state={'status':'uploaded_private','id':'QWBMWqFCaDc','original_audio_language':'en'}
        self.assertTrue(u.visibility_ready(state,{'language':'en'}))
        self.assertFalse(u.visibility_ready(state,{'language':'ru'}))
        self.assertFalse(u.visibility_ready({**state,'id':None},{'language':'en'}))
        self.assertTrue(u.visibility_ready({**state,'original_audio_language':'ru'},{}))

    async def test_the_saved_draft_of_this_video_is_reopened_on_its_own_channel(self):
        import youtube_studio_uploader as u
        page=MagicMock();page.goto=AsyncMock()
        button=page.get_by_role.return_value;button.click=AsyncMock()
        state={'id':'gzGF8ttaHm0','channel_id':'UC1'}
        with patch.object(u,'ensure_channel',new=AsyncMock()) as channel:
            await u.reopen_draft(page,state)
        channel.assert_awaited_once_with(page,state)
        self.assertEqual(page.goto.await_args.args[0],'https://studio.youtube.com/video/gzGF8ttaHm0/edit')
        button.click.assert_awaited_once()
        self.assertRegex('Edit draft',page.get_by_role.call_args.kwargs['name'])
        page.set_input_files.assert_not_called()
