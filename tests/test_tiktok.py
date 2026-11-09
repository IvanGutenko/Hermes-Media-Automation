import asyncio,hashlib,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,MagicMock,Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import importlib.util
import tiktok_format as tt
HOST=importlib.util.find_spec('bs4') is not None and importlib.util.find_spec('google') is not None
CONTAINER=importlib.util.find_spec('playwright') is not None


@unittest.skipUnless(CONTAINER,'runs in the browser container')
class AccountSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tiktok_publisher
        self.t=tiktok_publisher
        self.tmp=tempfile.TemporaryDirectory()
        self.patch=patch.object(self.t,'SESSIONS',Path(self.tmp.name)/'sessions');self.patch.start()

    def tearDown(self):
        self.patch.stop();self.tmp.cleanup()

    async def test_an_account_without_a_stored_session_uses_the_browser_profile(self):
        browser=MagicMock();browser.new_context=AsyncMock()
        context,isolated=await self.t.account_context(browser,'one')
        self.assertIs(context,browser.contexts[0]);self.assertFalse(isolated)
        browser.new_context.assert_not_awaited()

    async def test_an_account_with_a_stored_session_never_touches_the_profile_session(self):
        self.t.SESSIONS.mkdir();path=self.t.session_file('Two');path.write_text('{}')
        browser=MagicMock();browser.new_context=AsyncMock(return_value='isolated')
        context,isolated=await self.t.account_context(browser,'two')
        self.assertEqual((context,isolated),('isolated',True))
        browser.new_context.assert_awaited_once_with(storage_state=str(path))

    async def test_stored_session_is_private_and_context_closes_even_if_saving_fails(self):
        context=MagicMock();context.close=AsyncMock()
        async def state(path):Path(path).write_text('{"cookies":[]}')
        context.storage_state=state
        await self.t.close_account_context(context,'two')
        self.assertEqual(self.t.session_file('two').stat().st_mode&0o777,0o600)
        self.assertEqual(self.t.SESSIONS.stat().st_mode&0o777,0o700)
        context.close.assert_awaited_once()
        context.storage_state=AsyncMock(side_effect=RuntimeError('gone'));context.close.reset_mock()
        with self.assertRaises(RuntimeError):await self.t.close_account_context(context,'two')
        context.close.assert_awaited_once()

    def _page(self,*hrefs_by_poll):
        page=MagicMock();page.goto=AsyncMock();page.bring_to_front=AsyncMock();page.wait_for_timeout=AsyncMock()
        page.locator.return_value.evaluate_all=AsyncMock(side_effect=list(hrefs_by_poll))
        return page

    async def test_a_studio_list_that_draws_slowly_is_waited_for(self):
        page=self._page([],[],['/@me/video/7300000000000000001'])
        self.assertEqual(await self.t.studio_ids(page,'me'),['7300000000000000001'])
        page.bring_to_front.assert_awaited()

    async def test_a_studio_list_that_stays_empty_gives_up_with_nothing(self):
        page=self._page(*[[] for _ in range(self.t.STUDIO_POLLS)])
        self.assertEqual(await self.t.studio_ids(page,'me'),[])

    async def test_a_post_missing_from_a_broken_public_grid_is_found_in_studio_and_nothing_is_resent(self):
        state={'username':'me','codes_before':['7300000000000000001'],'caption':'c'}
        url='https://www.tiktok.com/@me/video/7300000000000000002'
        with patch.object(self.t,'grid_ids',new=AsyncMock(return_value=[])),\
                patch.object(self.t,'studio_ids',new=AsyncMock(return_value=['7300000000000000002','7300000000000000001'])),\
                patch.object(self.t,'verify_id',new=AsyncMock(return_value=url)) as verify,patch.object(self.t,'compose',new=AsyncMock()) as compose:
            await self.t.locate_and_verify(MagicMock(),MagicMock(),state,None)
        self.assertEqual(state['code'],'7300000000000000002');self.assertEqual(state['url'],url)
        self.assertEqual(verify.await_count,1);compose.assert_not_awaited()

    def test_only_tiktok_data_leaves_the_browser_profile(self):
        import tiktok_session as session
        state={'cookies':[{'name':'a','domain':'.tiktok.com'},{'name':'b','domain':'www.tiktok.com'},{'name':'c','domain':'.google.com'},
            {'name':'d','domain':'nottiktok.com'}],'origins':[{'origin':'https://www.tiktok.com'},{'origin':'https://accounts.google.com'}]}
        kept=session.only_tiktok(state)
        self.assertEqual([c['name'] for c in kept['cookies']],['a','b'])
        self.assertEqual([o['origin'] for o in kept['origins']],['https://www.tiktok.com'])


class VideoIdTests(unittest.TestCase):
    def test_only_the_own_account_videos_are_found_in_page_order(self):
        hrefs=['/@me/video/7300000000000000001','https://www.tiktok.com/@me/video/7300000000000000002?x=1',
            '/@other/video/7300000000000000003','https://evil.test/@me/video/7300000000000000004','/@me/video/7300000000000000001',
            '/@me/video/12']
        ids=tt.video_ids(hrefs,'me')
        self.assertEqual(ids,['7300000000000000001','7300000000000000002'])
        self.assertEqual(tt.new_ids(['7300000000000000001'],ids),['7300000000000000002'])
        self.assertEqual(tt.video_url('me','7300000000000000002'),'https://www.tiktok.com/@me/video/7300000000000000002')

    def test_the_handle_is_matched_case_insensitively_but_not_as_a_prefix(self):
        self.assertEqual(tt.video_ids(['/@Me/video/7300000000000000001'],'me'),['7300000000000000001'])
        self.assertEqual(tt.video_ids(['/@me.extra/video/7300000000000000001'],'me'),[])


@unittest.skipUnless(HOST,'runs on the server host')
class DistributionTests(unittest.TestCase):
    def setUp(self):
        import content_distribution as distribution
        self.d=distribution
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.out=root/'output';self.out.mkdir();self.data=root/'data';self.data.mkdir()
        self.config=self.data/'brands.json';self.root=root

    def tearDown(self):self.tmp.cleanup()

    def settings(self,**values):
        self.config.write_text(json.dumps({'wrongdev':{'publishing':{'tiktok':{'enabled':True,'target':'handle.one',**values}}}}))
        return [patch.object(self.d,'BASE_DIR',self.root),patch.object(self.d,'DATA',self.data),patch.object(self.d,'CONFIG',self.config)]

    def test_tiktok_refuses_an_image_and_never_touches_the_browser(self):
        image=self.out/'a.jpg';image.write_bytes(b'x');browser=Mock()
        for p in self.settings():p.start()
        try:
            with patch.object(self.d,'run_browser',browser),self.assertRaises(RuntimeError):
                self.d.publish('wrongdev','tiktok',{'source_id':'post-1','title':'T','text':'x','image':str(image)})
        finally:patch.stopall()
        browser.assert_not_called()

    def test_tiktok_video_is_published_once_with_ai_label_and_account_interval(self):
        media=self.out/'runs/r1/short.mp4';media.parent.mkdir(parents=True);media.write_bytes(b'video')
        payload={'source_id':'video-r1','title':'Title','text':'Body @WrongDevp.','video':str(media)}
        key=hashlib.sha256(json.dumps({'brand':'wrongdev','target':'handle.one','source':'video-r1','kind':'video'},sort_keys=True).encode()).hexdigest()
        proof={'status':'published','username':'handle.one','source_id':'video-r1','sha256':hashlib.sha256(b'video').hexdigest(),
            'code':'7300000000000000001','url':'https://www.tiktok.com/@handle.one/video/7300000000000000001','published_at':100,'verified_at':120}
        (self.out/('native_tiktok_'+key+'.json')).write_text(json.dumps(proof))
        browser=Mock(return_value=Mock(returncode=0,stdout='{}',stderr=''))
        for p in self.settings(account_interval_seconds=1000):p.start()
        try:
            with patch.object(self.d,'run_browser',browser),patch.object(self.d.time,'time',return_value=500):
                done=self.d.publish('wrongdev','tiktok',payload)
                later=self.d.publish('wrongdev','tiktok',{**payload,'source_id':'video-r2'})
        finally:patch.stopall()
        self.assertEqual(done['status'],'published');self.assertEqual(later['status'],'scheduled')
        job=json.loads((self.out/('tiktok_native_job_'+key+'.json')).read_text())
        self.assertTrue(job['ai_label']);self.assertEqual(job['username'],'handle.one');self.assertNotIn('@',job['caption'])
        self.assertIn('/app/scripts/tiktok_publisher.py',browser.call_args.args[0])
        self.assertEqual(browser.call_count,1)


@unittest.skipUnless(CONTAINER,'runs in the browser container (needs Playwright)')
class PublisherSafetyTests(unittest.TestCase):
    """Run in the browser container: they import the Playwright-based publisher."""
    def setUp(self):
        import tiktok_publisher as tp
        self.tp=tp
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.media=self.root/'short.mp4';self.media.write_bytes(b'video');self.sha=hashlib.sha256(b'video').hexdigest()
        self.key='a'*64
        self.job_path=self.root/('tiktok_native_job_'+self.key+'.json')
        self.job_path.write_text(json.dumps({'key':self.key,'username':'handle.one','source_id':'video-1','caption':'Caption text',
            'video':str(self.media),'sha256':self.sha,'ai_label':True}))
        self.receipt=self.root/('native_tiktok_'+self.key+'.json')

    def tearDown(self):self.tmp.cleanup()

    def run_publish(self,**patches):
        page=MagicMock();page.close=AsyncMock();page.goto=AsyncMock()
        context=MagicMock();context.new_page=AsyncMock(return_value=page)
        browser=MagicMock();browser.contexts=[context];browser.close=AsyncMock()
        fake=MagicMock();fake.__aenter__=AsyncMock(return_value=object());fake.__aexit__=AsyncMock(return_value=False)
        defaults={'ROOT':self.root,'geometry':lambda path:(1080,1920,40.0),'async_playwright':lambda:fake,
            'connect':AsyncMock(return_value=browser),'ensure_signed_in':AsyncMock(),'anonymous_check':AsyncMock(),
            'grid_ids':AsyncMock(return_value=['1'])}
        defaults.update(patches)
        for name,value in defaults.items():patch.object(self.tp,name,value).start()
        try:return asyncio.run(self.tp.publish(self.job_path))
        finally:patch.stopall()

    def test_logged_out_browser_stops_before_any_upload(self):
        compose=AsyncMock()
        with self.assertRaises(self.tp.NeedsLogin):
            self.run_publish(ensure_signed_in=AsyncMock(side_effect=self.tp.NeedsLogin('x')),compose=compose)
        compose.assert_not_called()
        self.assertEqual(json.loads(self.receipt.read_text())['status'],'new')

    def test_a_posted_video_is_found_on_the_profile_and_never_uploaded_again(self):
        self.receipt.write_text(json.dumps({'status':'posted_unverified','username':'handle.one','source_id':'video-1','caption':'Caption text',
            'sha256':self.sha,'media_kind':'video','share_attempted_at':50,'codes_before':['1']}))
        compose=AsyncMock()
        async def found(context,page,state,path):
            state.update(code='2',url='https://www.tiktok.com/@handle.one/video/2');self.tp.save(path,state)
        state=self.run_publish(compose=compose,locate_and_verify=found)
        compose.assert_not_called();self.assertEqual(state['status'],'published');self.assertEqual(state['published_at'],50)

    def test_unfound_video_after_post_attempt_blocks_a_replacement(self):
        self.receipt.write_text(json.dumps({'status':'publishing','username':'handle.one','source_id':'video-1','caption':'Caption text',
            'sha256':self.sha,'media_kind':'video','share_attempted_at':50,'codes_before':['1']}))
        compose=AsyncMock()
        with self.assertRaises(RuntimeError):
            self.run_publish(compose=compose,locate_and_verify=AsyncMock(side_effect=RuntimeError('not visible')))
        compose.assert_not_called()


if __name__=='__main__':unittest.main()
