import asyncio,hashlib,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
try:
    import instagram_publisher as ig
except ModuleNotFoundError as exc:
    raise unittest.SkipTest('runs in the browser container (needs Playwright): '+str(exc))


class FakePlaywright:
    async def __aenter__(self):return object()
    async def __aexit__(self,*a):return False


def fake_browser():
    page=MagicMock();page.close=AsyncMock();page.locator.return_value.inner_text=AsyncMock(return_value='visible text')
    page.screenshot=AsyncMock();page.goto=AsyncMock();page.bring_to_front=AsyncMock()
    context=MagicMock();context.new_page=AsyncMock(return_value=page)
    browser=MagicMock();browser.contexts=[context];browser.close=AsyncMock()
    return browser


class PublisherSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.media=self.root/'short.mp4';self.media.write_bytes(b'video')
        self.sha=hashlib.sha256(b'video').hexdigest()
        self.key='a'*64
        self.job={'key':self.key,'username':'thinkscope.hub','source_id':'video-1','caption':'Caption text','video':str(self.media),
            'image':None,'sha256':self.sha}
        self.job_path=self.root/('instagram_native_job_'+self.key+'.json');self.job_path.write_text(json.dumps(self.job))
        self.receipt=self.root/('native_instagram_'+self.key+'.json')

    def tearDown(self):self.tmp.cleanup()

    def run_publish(self,**patches):
        defaults={'ROOT':self.root,'geometry':lambda path:(1080,1920,40.0),'async_playwright':lambda:FakePlaywright(),
            'connect':AsyncMock(return_value=fake_browser()),'ensure_signed_in':AsyncMock(),
            'anonymous_check':AsyncMock(),'grid_codes':AsyncMock(return_value=['OLD'])}
        defaults.update(patches)
        managers=[patch.object(ig,name,value) for name,value in defaults.items()]
        for m in managers:m.start()
        try:return asyncio.run(ig.publish(self.job_path))
        finally:
            for m in managers:m.stop()

    def test_logged_out_browser_stops_before_any_upload_and_keeps_the_post_resubmittable(self):
        compose=AsyncMock()
        with self.assertRaises(ig.NeedsLogin):
            self.run_publish(ensure_signed_in=AsyncMock(side_effect=ig.NeedsLogin('not signed in')),compose=compose)
        compose.assert_not_called()
        state=json.loads(self.receipt.read_text())
        self.assertEqual(state['status'],'new');self.assertIn('NeedsLogin',state['last_error'])

    def test_a_shared_post_is_found_on_the_profile_and_never_uploaded_again(self):
        self.receipt.write_text(json.dumps({'status':'posted_unverified','username':'thinkscope.hub','source_id':'video-1',
            'caption':'Caption text','sha256':self.sha,'media_kind':'video','share_attempted_at':50,'codes_before':['OLD']}))
        compose=AsyncMock()
        async def found(context,page,state,path):
            state.update(code='NEW',url='https://www.instagram.com/reel/NEW/');ig.save(path,state)
        state=self.run_publish(compose=compose,locate_and_verify=found)
        compose.assert_not_called()
        self.assertEqual(state['status'],'published');self.assertEqual(state['url'],'https://www.instagram.com/reel/NEW/')
        self.assertEqual(state['published_at'],50)
        self.assertEqual(json.loads(self.receipt.read_text())['code'],'NEW')

    def test_unfound_post_after_a_share_attempt_blocks_a_replacement_upload(self):
        self.receipt.write_text(json.dumps({'status':'publishing','username':'thinkscope.hub','source_id':'video-1',
            'caption':'Caption text','sha256':self.sha,'media_kind':'video','share_attempted_at':50,'codes_before':['OLD']}))
        compose=AsyncMock()
        with self.assertRaises(RuntimeError):
            self.run_publish(compose=compose,locate_and_verify=AsyncMock(side_effect=RuntimeError('not visible yet')))
        compose.assert_not_called()
        self.assertEqual(json.loads(self.receipt.read_text())['status'],'publishing')

    def test_another_account_post_awaiting_readback_blocks_a_new_submission(self):
        other='b'*64
        (self.root/('native_instagram_'+other+'.json')).write_text(json.dumps({'status':'posted_unverified','username':'thinkscope.hub',
            'share_attempted_at':1}))
        compose=AsyncMock()
        with self.assertRaises(RuntimeError):
            self.run_publish(compose=compose)
        compose.assert_not_called()

    def test_changed_media_or_caption_is_refused(self):
        self.media.write_bytes(b'different')
        with self.assertRaises(RuntimeError):self.run_publish()
        self.media.write_bytes(b'video')
        self.receipt.write_text(json.dumps({'status':'new','username':'thinkscope.hub','source_id':'video-1',
            'caption':'Older caption','sha256':self.sha,'media_kind':'video','started_at':1}))
        with self.assertRaises(RuntimeError):self.run_publish()

    def test_a_horizontal_video_is_not_posted_as_a_reel(self):
        compose=AsyncMock()
        with self.assertRaises(RuntimeError):
            self.run_publish(geometry=lambda path:(1920,1080,40.0),compose=compose)
        compose.assert_not_called()

    def test_first_attempt_records_the_profile_before_composing_then_verifies(self):
        async def compose(page,state,path,raw,dry_run):
            state.update(status='posted_unverified',share_attempted_at=70);ig.save(path,state)
        async def found(context,page,state,path):
            self.assertEqual(state['codes_before'],['OLD'])
            state.update(code='NEW',url='https://www.instagram.com/reel/NEW/');ig.save(path,state)
        state=self.run_publish(compose=compose,locate_and_verify=found)
        self.assertEqual(state['status'],'published');self.assertEqual(state['published_at'],70)


class FakeLocator:
    def __init__(self,visible=lambda:False,on_click=None):
        self.visible,self.on_click,self.clicks=visible,on_click,0
    first=property(lambda self:self)
    async def count(self):return 1 if self.visible() else 0
    async def is_visible(self):return self.visible()
    async def click(self):
        self.clicks+=1
        if self.on_click:self.on_click()


class FakeComposer:
    """Next button -> caption box, or an Instagram error dialog that Try again does not clear."""
    def __init__(self,error):
        self.step=0;self.error=error
        self.next=FakeLocator(lambda:self.step==0 and not self.error,self.advance)
        self.box=FakeLocator(lambda:self.step==1)
        self.problem=FakeLocator(lambda:self.error)
        self.retry=FakeLocator(lambda:self.error)
    def advance(self):self.step=1
    def get_by_role(self,role,name=None):
        return self.next if 'Next' in name.pattern else self.retry
    def get_by_text(self,pattern):return self.problem
    async def wait_for_timeout(self,ms):pass


class CaptionStepTests(unittest.TestCase):
    def run_step(self,page):
        with patch.object(ig,'snap',AsyncMock()):
            return asyncio.run(ig.to_caption_step(page,page.box))

    def test_next_is_clicked_until_the_caption_box_appears(self):
        page=FakeComposer(error=False);self.run_step(page)
        self.assertEqual(page.next.clicks,1)

    def test_something_went_wrong_is_retried_once_then_fails_fast(self):
        page=FakeComposer(error=True)
        with self.assertRaises(RuntimeError) as caught:self.run_step(page)
        self.assertIn('Something went wrong',str(caught.exception));self.assertEqual(page.retry.clicks,1)
        self.assertEqual(page.next.clicks,0)


class FakeDialog:
    last=property(lambda self:self)
    def __init__(self,page):self.page=page
    async def evaluate(self,js):return self.page.window()


class FakeFramedComposer:
    """Crop step -> Edit step -> caption box, where the composer starts on a square crop of the media."""
    def __init__(self,start=(713,713),fixed=(401,713),fix_works=True,unmeasurable=False,caption_window=None):
        self.start,self.fixed,self.fix_works,self.unmeasurable=start,fixed,fix_works,unmeasurable
        self.caption_window=caption_window
        self.step=0;self.chosen=None;self.menu_open=False;self.log=[]
        self.next=FakeLocator(lambda:self.step<2,self.advance)
        self.box=FakeLocator(lambda:self.step==2)
        self.crop=FakeLocator(lambda:self.step==0,lambda:self.log.append('crop'))
        self.option=FakeLocator(lambda:True,self.pick)
        self.problem=FakeLocator(lambda:False);self.retry=FakeLocator(lambda:False)
    def advance(self):self.log.append('next');self.step+=1
    def pick(self):self.log.append('choose '+self.requested)
    def window(self):
        if self.step==2:return self.caption_window
        if self.unmeasurable and self.step==0:return None
        if self.chosen and self.fix_works:return self.fixed
        return self.start
    def get_by_role(self,role,name=None):
        if role=='dialog':return FakeDialog(self)
        if 'Next' in name.pattern:return self.next
        if name.search('Select crop'):return self.crop
        return self.retry
    def get_by_text(self,pattern):
        if 'wrong' in pattern.pattern:return self.problem
        self.requested=pattern.pattern.strip('^$');self.chosen=self.requested
        return self.option
    async def wait_for_timeout(self,ms):pass


class FrameStepTests(unittest.TestCase):
    def run_step(self,page,size=(720,1280),kind='video'):
        with patch.object(ig,'snap',AsyncMock()):
            return asyncio.run(ig.to_caption_step(page,page.box,size,kind))

    def test_a_reel_on_the_default_square_crop_is_set_to_9_16_before_any_next(self):
        page=FakeFramedComposer();self.run_step(page)
        self.assertEqual(page.log,['crop','choose 9:16','next','next'])

    def test_a_photo_is_set_to_original(self):
        page=FakeFramedComposer(start=(713,713),fixed=(713,401),caption_window=(713,401));self.run_step(page,(1280,720),'image')
        self.assertEqual(page.log,['crop','choose Original','next','next'])

    def test_an_already_whole_frame_is_left_alone(self):
        page=FakeFramedComposer(start=(401,713),caption_window=(401,713));self.run_step(page)
        self.assertEqual(page.log,['next','next'])

    def test_a_crop_that_does_not_take_stops_before_next_and_share(self):
        page=FakeFramedComposer(fix_works=False)
        with self.assertRaisesRegex(RuntimeError,'would crop'):self.run_step(page)
        self.assertNotIn('next',page.log)

    def test_a_preview_that_stays_unmeasurable_on_the_crop_step_stops_the_post(self):
        page=FakeFramedComposer(unmeasurable=True)
        with patch.object(ig,'FRAME_WAIT_SECONDS',-1),self.assertRaisesRegex(RuntimeError,'could not be measured'):self.run_step(page)
        self.assertEqual(page.log,[])

    def test_a_cropped_caption_step_is_never_reached_silently(self):
        page=FakeFramedComposer(start=(401,713),caption_window=(713,713))
        with self.assertRaisesRegex(RuntimeError,'would crop'):self.run_step(page)

    def test_without_a_known_size_the_steps_are_unchanged(self):
        page=FakeFramedComposer();self.run_step(page,None)
        self.assertEqual(page.log,['next','next'])


class Fake:
    """A locator that is visible or times out according to a script; or_/first return itself."""
    def __init__(self,visible=False,times_out=False,click=None):
        self.visible,self.times_out,self.click_hook,self.clicks=visible,times_out,click,0
    first=property(lambda self:self)
    last=property(lambda self:self)
    def or_(self,other):return self
    def filter(self,**kwargs):return self
    async def wait_for(self,timeout=None,state=None):
        if self.times_out:raise ig.PlaywrightTimeout('timeout')
    async def count(self):return 1 if self.visible else 0
    async def is_visible(self):return self.visible
    async def click(self,timeout=None):
        self.clicks+=1
        if self.click_hook:self.click_hook()
    def get_by_text(self,pattern):return self.row
    def get_by_role(self,role,name=None):return self


class AccountPage:
    """The profile of the wanted account only shows the edit link once it is the active account."""
    def __init__(self,active,listed=True):
        self.active,self.listed,self.gotos,self.switched=active,listed,[],[]
        self.login=Fake()
        self.chooser=Fake()
        def select():self.active=True;self.chooser.visible=False
        self.row=Fake(visible=listed,times_out=not listed,click=select)
    def goto(self,url,**kw):
        async def go():self.gotos.append(url)
        return go()
    def locator(self,selector):
        if 'accounts/edit' in selector:return Fake(visible=self.active,times_out=not self.active)
        if selector.startswith('svg'):return Fake()
        return self.login
    def get_by_role(self,role,name=None):
        if role=='dialog':
            dialog=Fake();dialog.row=self.row;return dialog
        return Fake()
    def get_by_text(self,pattern,**kwargs):
        if pattern.search('Turn on Notifications') or pattern.search('Log into Instagram'):return Fake()
        if pattern.search('Switch accounts'):return self.chooser
        if pattern.search('Switch'):
            def open_chooser():self.chooser.visible=True
            return Fake(visible=True,click=open_chooser)
        if pattern.search('wrongdevp'):return self.row
        return Fake()
    async def wait_for_timeout(self,ms):pass


class SignInTests(unittest.TestCase):
    def run_sign_in(self,page,switch=None):
        async def default_switch(p,username):
            page.switched.append(username);page.active=True
        with patch.object(ig,'switch_account',switch or default_switch):
            return asyncio.run(ig.ensure_signed_in(page,'wrongdevp'))

    def test_the_active_account_is_used_without_switching(self):
        page=AccountPage(active=True);self.run_sign_in(page)
        self.assertEqual(page.switched,[]);self.assertEqual(page.gotos,[ig.SITE+'/wrongdevp/'])

    def test_another_active_account_is_switched_to_the_wanted_one_and_rechecked(self):
        page=AccountPage(active=False);self.run_sign_in(page)
        self.assertEqual(page.switched,['wrongdevp']);self.assertEqual(len(page.gotos),2)

    def test_a_switch_that_does_not_take_fails_instead_of_posting_from_the_wrong_account(self):
        page=AccountPage(active=False)
        async def noop(p,username):page.switched.append(username)
        with self.assertRaisesRegex(RuntimeError,'did not show'):self.run_sign_in(page,noop)
        self.assertEqual(page.switched,['wrongdevp'])

    def test_a_logged_out_browser_still_needs_the_owner(self):
        page=AccountPage(active=False);page.locator=lambda selector:Fake(visible=True) if 'accounts/edit' not in selector else Fake(times_out=False,visible=False)
        with self.assertRaises(ig.NeedsLogin):self.run_sign_in(page)
        self.assertEqual(page.switched,[])

    def test_switching_clicks_the_wanted_account_and_waits_for_the_dialog_to_close(self):
        page=AccountPage(active=False)
        closed=MagicMock();closed.not_to_be_visible=AsyncMock()
        with patch.object(ig,'expect',return_value=closed):asyncio.run(ig.switch_account(page,'wrongdevp'))
        self.assertEqual(page.row.clicks,1);closed.not_to_be_visible.assert_awaited_once()
        self.assertFalse(page.chooser.visible)

    def test_an_account_that_was_never_signed_in_here_needs_the_owner(self):
        page=AccountPage(active=False,listed=False)
        with self.assertRaisesRegex(ig.NeedsLogin,'wrongdevp'):asyncio.run(ig.switch_account(page,'wrongdevp'))
        self.assertEqual(page.row.clicks,0)


if __name__=='__main__':unittest.main()
