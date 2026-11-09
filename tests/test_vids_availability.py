"""A loaded editor missing video generation is different from a slow or failed page."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import vids_automator as vids


class Editor:
    def __init__(self,stages):
        self.stages=stages;self.stage=0;self.clicked=[];self.url='https://docs.google.com/videos/d/test/edit'
        self.reload=AsyncMock(side_effect=self.reloaded)
        self.locator=Mock();self.locator.return_value.inner_text=AsyncMock(return_value='Loaded editor: Avatar Voiceover Image')
    async def reloaded(self,**kw):self.stage=min(self.stage+1,len(self.stages)-1)
    def get_by_role(self,role,*,name):
        pattern=name.pattern
        key=next(k for k,token in [('create','Create AI'),('portrait','portrait'),('rename','Rename'),
            ('blank','Blank vid'),('box','Describe'),('reopen','Generate an AI'),('voice','Generate a voiceover')] if token in pattern)
        result=Mock();result.first=result;result.or_.return_value=result
        result.is_visible=AsyncMock(side_effect=lambda:key in self.stages[self.stage])
        async def wait(**kw):
            if key=='voice' and key not in self.stages[self.stage]:raise TimeoutError('Editor not loaded')
        async def click():self.clicked.append(key)
        result.wait_for=AsyncMock(side_effect=wait);result.click=AsyncMock(side_effect=click)
        return result


class AvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_blank_editor_opens_the_available_generation_panel(self):
        page=Editor([{'rename','voice','reopen'}]);state={}
        await vids.open_generation_panel(page,state)
        self.assertEqual(page.clicked,['reopen']);page.reload.assert_not_awaited()
        self.assertNotIn('availability_observations',state)

    async def test_missing_option_is_confirmed_after_reload(self):
        page=Editor([{'rename','voice'}]);state={}
        with self.assertRaises(vids.FeatureUnavailable):await vids.open_generation_panel(page,state)
        self.assertEqual(len(state['availability_observations']),2)
        page.reload.assert_awaited_once();self.assertEqual(page.clicked,[])

    async def test_missing_option_in_start_chooser_never_clicks_orientation_or_blank(self):
        page=Editor([{'portrait','blank'}]);state={}
        ready=Mock();ready.to_be_enabled=AsyncMock()
        with patch.object(vids,'expect',return_value=ready):
            with self.assertRaises(vids.FeatureUnavailable):await vids.open_generation_panel(page,state)
        self.assertEqual(page.clicked,[])
        self.assertEqual([o['surface'] for o in state['availability_observations']],['start_chooser','start_chooser'])
        self.assertEqual(ready.to_be_enabled.await_count,2)

    async def test_an_option_appearing_after_reload_is_used(self):
        page=Editor([{'rename','voice'},{'rename','voice','reopen'}]);state={}
        await vids.open_generation_panel(page,state)
        self.assertEqual(page.clicked,['reopen']);self.assertEqual(len(state['availability_observations']),1)

    async def test_a_loading_or_login_failure_is_not_classified_as_unavailable(self):
        page=Editor([{'rename'}]);state={}
        with self.assertRaises(TimeoutError):await vids.open_generation_panel(page,state)
        self.assertNotIn('availability_observations',state);page.reload.assert_not_awaited()

if __name__=='__main__':unittest.main()
