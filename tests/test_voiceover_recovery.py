"""An interrupted Insert must recover, while uncertain or finished audio must not regenerate."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import vids_voiceover as voice


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.receipt=Path(self.tmp.name)/'receipt.json'
        self.state={'submitted_at':time.time()-86400,'status':'voice_submitted'}
        self.empty={'script_matches':True,'voice_matches':True,'insert_enabled':True,
            'empty_scene':True,'audio_tracks':[],'saved':True,'progress':False,'ready':False}
        self.page=Mock();self.page.reload=AsyncMock()
        self.page.get_by_role.return_value.wait_for=AsyncMock()

    async def reconcile(self,observations):
        with patch.object(voice,'observe_voiceover',new=AsyncMock(side_effect=observations)), \
                patch.object(voice,'open_voiceover',new=AsyncMock()):
            return await voice.reconcile_voiceover(self.page,self.state,self.receipt,'Точный текст','Knox')

    async def test_legacy_click_intent_recovers_only_after_reload_confirms_empty_saved_document(self):
        original=self.state['submitted_at']
        self.assertTrue(await self.reconcile([self.empty,self.empty]))
        self.page.reload.assert_awaited_once()
        saved=json.loads(self.receipt.read_text())
        self.assertEqual(saved['submitted_at'],original)
        self.assertEqual(saved['empty_scene_recoveries'],1)
        self.assertEqual(len(saved['reconciliation_observations']),1)

    async def test_finished_audio_is_never_submitted_again(self):
        self.assertFalse(await self.reconcile([{**self.empty,'ready':True,'empty_scene':False}]))
        self.page.reload.assert_not_awaited()

    async def test_ambiguous_document_never_authorizes_reinsertion(self):
        for key,value in [('script_matches',False),('voice_matches',False),('insert_enabled',False),
                          ('empty_scene',False),('saved',False),('progress',True),
                          ('audio_tracks',['Existing audio - Knox']),('audio_tracks',None)]:
            with self.subTest(key=key):
                self.assertFalse(await self.reconcile([{**self.empty,key:value}]))
        self.page.reload.assert_not_awaited()

    async def test_inflight_request_waits_even_when_scene_is_still_empty(self):
        self.state['submitted_at']=time.time()
        self.assertFalse(await self.reconcile([self.empty]))
        self.page.reload.assert_not_awaited()

    async def test_result_appearing_during_reload_prevents_another_request(self):
        self.assertFalse(await self.reconcile([self.empty,{**self.empty,'ready':True,'empty_scene':False}]))
        self.assertNotIn('empty_scene_recoveries',self.state)

    async def test_repeated_observed_failure_is_bounded_and_preserves_evidence(self):
        self.state['empty_scene_recoveries']=voice.MAX_EMPTY_SCENE_RECOVERIES
        with self.assertRaisesRegex(RuntimeError,'recovery exhausted'):
            await self.reconcile([self.empty,self.empty])
        saved=json.loads(self.receipt.read_text())
        self.assertTrue(saved['terminal_failure_observed'])
        self.assertTrue(saved['reconciliation_observations'])

if __name__=='__main__':unittest.main()
