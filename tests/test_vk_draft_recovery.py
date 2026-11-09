"""Only a provably owned, frozen residual may free the shared VK composer."""
import hashlib,importlib.util,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import AsyncMock,MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))

@unittest.skipUnless(importlib.util.find_spec('playwright'),'runs in browser container')
class OwnedResidual(unittest.TestCase):
    def setUp(self):
        from vk_photo_publisher import is_owned_residual
        self.check=is_owned_residual;self.now=time.time()
        self.old={'key':'a'*64,'group_id':123,'text':'Our exact post https://example.org/1',
            'status':'publishing','publish_requested_at':self.now-13*3600,'sha256':'b'*64,'image_uploaded_sha256':'b'*64}
        self.text=self.old['text']

    def test_old_uncertain_owned_residue_can_be_archived_without_changing_receipt(self):
        before=dict(self.old)
        self.assertTrue(self.check(self.old,123,self.text,self.now))
        self.assertEqual(self.old,before)

    def test_unrelated_group_text_link_or_unproven_image_is_preserved(self):
        self.assertFalse(self.check(self.old,124,self.text,self.now))
        self.assertFalse(self.check(self.old,123,self.text.replace('/1','/2'),self.now))
        self.assertFalse(self.check({**self.old,'image_uploaded_sha256':'c'*64},123,self.text,self.now))
        self.assertFalse(self.check({**self.old,'key':'unknown'},123,self.text,self.now))

    def test_recent_send_unsent_and_manual_preview_are_preserved(self):
        self.assertFalse(self.check({**self.old,'publish_requested_at':self.now-3600},123,self.text,self.now))
        for status in ('new','preparing','draft_preview','error'):
            self.assertFalse(self.check({**self.old,'status':status},123,self.text,self.now))

    def test_published_residue_requires_independent_readback(self):
        self.assertFalse(self.check({**self.old,'status':'published'},123,self.text,self.now))
        self.assertTrue(self.check({**self.old,'status':'published','public_verified_at':self.now},123,self.text,self.now))

@unittest.skipUnless(importlib.util.find_spec('playwright'),'runs in browser container')
class ArchiveRecovery(unittest.IsolatedAsyncioTestCase):
    async def run_recovery(self,change_file=False,change_job=False):
        import vk_photo_publisher as v
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);image=root/'image.jpg';image.write_bytes(b'original verified input')
            old={'key':'a'*64,'source_id':'post-128','group_id':123,'text':'Exact original text',
                 'image':str(image),'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
                 'status':'publishing','publish_requested_at':time.time()-13*3600}
            old['image_uploaded_sha256']=old['sha256']
            path=root/('native_vk_photo_'+old['key']+'.json');path.write_text(json.dumps(old))
            frozen={**old,'text':'Changed text'} if change_job else old
            (root/('vk_photo_job_'+old['key']+'.json')).write_text(json.dumps(frozen))
            if change_file:image.write_bytes(b'changed')
            page=MagicMock();page.keyboard.press=AsyncMock();page.screenshot=AsyncMock()
            button=MagicMock();button.click=AsyncMock();page.get_by_role.return_value=button
            box=MagicMock();box.aria_snapshot=AsyncMock(return_value='textbox: Exact original text');box.wait_for=AsyncMock()
            with patch.object(v,'ROOT',root),patch.object(v,'editor_text',AsyncMock(return_value=old['text'])):
                result=await v.clear_owned_residual_draft(page,box,123)
            after=json.loads(path.read_text())
            self.assertEqual(after['status'],'publishing')
            self.assertNotIn('public_verified_at',after)
            self.assertEqual(after['publish_requested_at'],old['publish_requested_at'])
            if result:
                archive=Path(after['residual_draft_archive']);self.assertTrue(archive.is_file())
                self.assertEqual(json.loads(archive.read_text())['text'],old['text'])
                page.screenshot.assert_awaited_once();button.click.assert_awaited_once()
                self.assertEqual(page.get_by_role.call_args.args,('button',))
                box.wait_for.assert_awaited_once_with(state='hidden')
            else:button.click.assert_not_awaited();page.screenshot.assert_not_awaited()
            return result

    async def test_frozen_old_send_is_archived_and_cleared_without_faking_publication(self):
        self.assertTrue(await self.run_recovery())

    async def test_changed_original_file_preserves_draft(self):
        self.assertFalse(await self.run_recovery(change_file=True))

    async def test_changed_frozen_job_preserves_draft(self):
        self.assertFalse(await self.run_recovery(change_job=True))

if __name__=='__main__':unittest.main()
