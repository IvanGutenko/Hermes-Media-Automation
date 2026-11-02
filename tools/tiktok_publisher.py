#!/usr/bin/env python3
"""Own-account TikTok video publication (TikTok Studio) with a durable receipt and profile readback.

Same safety rule as Instagram: once Post was clicked the video is never uploaded again; a retry only
looks for it on the profile. `--dry-run` uploads, fills the caption and AI label, then discards.
"""
import asyncio
import fcntl
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from playwright.async_api import async_playwright, expect, TimeoutError as PlaywrightTimeout
from browser_session import connect
import instagram_format as fmt
import tiktok_format as tt
from instagram_publisher import save, geometry

ROOT=Path('/data/output')
SITE='https://www.tiktok.com'
UPLOAD=SITE+'/tiktokstudio/upload'
ACK=re.compile(r'(Your video (is being|has been) uploaded|Video (published|uploaded)|Upload another video|Manage your posts|Ваше видео)',re.I)
FAILED=re.compile(r"(couldn.t (be )?post|failed to (post|upload)|Не удалось (опубликовать|загрузить))",re.I)
# TikTok's web site has no account switcher and one browser profile holds one session, so every other own
# account keeps its session here (next to the profile's own cookies) and works in an isolated browser context.
SESSIONS=Path('/data/profile/factory_sessions')


class NeedsLogin(RuntimeError):
    pass


def session_file(handle):
    return SESSIONS/('tiktok_'+handle.casefold()+'.json')


async def store_session(context,handle):
    SESSIONS.mkdir(mode=0o700,exist_ok=True)
    target=session_file(handle);temp=target.with_suffix('.tmp')
    await context.storage_state(path=str(temp))
    temp.chmod(0o600);temp.replace(target)


async def account_context(browser,handle):
    """(context, isolated): the account's own stored session when it has one, otherwise the browser profile's."""
    if session_file(handle).exists():
        return await browser.new_context(storage_state=str(session_file(handle))),True
    return browser.contexts[0],False


async def close_account_context(context,handle):
    """Keep the cookies TikTok rotated during the run, and leave nothing of the account in the browser."""
    try:await store_session(context,handle)
    finally:await context.close()


async def snap(page,tag):
    try:
        folder=ROOT/'tiktok_debug';folder.mkdir(exist_ok=True)
        await page.screenshot(path=str(folder/(time.strftime('%H%M%S')+'_'+tag+'.png')),timeout=5000)
    except Exception:pass


async def ensure_signed_in(page,handle):
    """/profile sends a signed-in user to their own /@handle; anyone else lands on a login page."""
    await page.goto(SITE+'/profile',wait_until='domcontentloaded',timeout=60000)
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        match=re.fullmatch(r'/@([A-Za-z0-9_.]{1,24})/?',re.sub(r'^https?://[^/]+','',page.url).split('?')[0])
        if match:
            if match[1].casefold()!=handle.casefold():raise RuntimeError('The signed-in TikTok account is not the approved profile')
            return
        if '/login' in page.url:break
        await asyncio.sleep(1)
    raise NeedsLogin('TikTok is not signed in in the factory browser; the owner must log in once')


async def grid_ids(page,handle):
    await page.goto(SITE+'/@'+handle,wait_until='domcontentloaded',timeout=60000)
    await page.wait_for_timeout(6000)
    hrefs=await page.locator('a[href*="/video/"]').evaluate_all('(es)=>es.map(e=>e.getAttribute("href"))')
    return tt.video_ids(hrefs,handle)


async def dismiss_notices(page):
    """TikTok asks to turn on automatic content (copyright) checks before editing; accept it."""
    # Also the product-tour tooltip ("New editing features added"), which blocks pointer events.
    notice=page.get_by_role('button',name=re.compile(r'^(Turn on|Включить|Got it|Понятно)$',re.I))
    for _ in range(10):
        if await notice.count() and await notice.first.is_visible():
            await snap(page,'notice');await notice.first.click();await page.wait_for_timeout(2000)
        else:
            await page.wait_for_timeout(1000)
    failure=page.get_by_text(re.compile(r"^(Couldn.t upload|Upload failed)",re.I))
    if await failure.count() and await failure.first.is_visible():
        await snap(page,'upload_failed')
        raise RuntimeError('TikTok Studio reported that the upload failed')


async def switch_on(page,text):
    """Turn on the switch/checkbox in the settings row labelled `text` and confirm that it is on."""
    label=page.get_by_text(text,exact=True).first
    await label.wait_for(timeout=30000)
    await label.scroll_into_view_if_needed()
    # The control is the first switch that follows the label's own text in the page, never a wider
    # container: the settings section holds several switches and the first one is already on.
    control=page.locator('xpath=(//*[normalize-space()="'+text+'"])[last()]/following::*[@role="switch" or @role="checkbox"][1]')
    if not await control.is_checked():
        # The visible thumb overlays the real input, so a normal click is intercepted.
        await control.click(force=True)
        # "Labeling AI-generated content" asks for a confirmation before the switch turns on.
        confirm=page.get_by_role('button',name=re.compile(r'^(Turn on|Включить)$',re.I))
        try:await confirm.first.click(timeout=8000)
        except PlaywrightTimeout:pass
    await expect(control).to_be_checked(timeout=10000)


async def compose(page,state,path,raw,dry_run):
    await page.goto(UPLOAD,wait_until='domcontentloaded',timeout=60000)
    chooser=page.locator('input[type=file]').first
    await chooser.wait_for(state='attached',timeout=60000)
    await snap(page,'upload_page')
    state.update(status='uploading',upload_attempted_at=time.time());save(path,state)
    await chooser.set_input_files(str(raw),timeout=120000)
    editor=page.locator('[contenteditable=true]').first
    await editor.wait_for(timeout=240000)
    await dismiss_notices(page)
    # TikTok pre-fills the caption with the file name; replace it with the reviewed caption.
    await editor.click();await page.keyboard.press('Control+A');await page.keyboard.press('Backspace')
    await page.keyboard.insert_text(state['caption'])
    if not fmt.caption_visible(await editor.inner_text(),state['caption']):
        raise RuntimeError('TikTok editor did not retain the reviewed caption')
    if state.get('ai_label'):
        more=page.get_by_text(re.compile(r'^(Show more|Ещё|Показать больше)$',re.I)).first
        if await more.count() and await more.is_visible():await more.click()
        # Only the AI label: "Disclose post content" would mark the video as a brand promotion.
        await switch_on(page,'AI-generated content')
    await snap(page,'caption')
    post=page.locator('[data-e2e=post_video_button]').or_(page.get_by_role('button',name=re.compile(r'^(Post|Опубликовать)$'))).first
    await expect(post).to_be_enabled(timeout=300000)
    if dry_run:
        await page.goto(SITE+'/tiktokstudio/content',wait_until='domcontentloaded',timeout=60000)
        leave=page.get_by_role('button',name=re.compile(r'^(Discard|Leave|Выйти|Отменить)$',re.I))
        if await leave.count():await leave.first.click()
        await snap(page,'after_discard');return
    state.update(status='publishing',share_attempted_at=time.time());save(path,state)
    await post.click()
    now=page.get_by_role('button',name=re.compile(r'^(Post now|Опубликовать сейчас)$',re.I))
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        if await now.count() and await now.first.is_visible():await now.first.click()
        if await page.get_by_text(ACK).first.is_visible() or '/tiktokstudio/content' in page.url:
            state.update(status='posted_unverified',share_acknowledged_at=time.time());save(path,state);break
        if await page.get_by_text(FAILED).first.is_visible():
            state.update(status='share_failed_visible');save(path,state)
            raise RuntimeError('TikTok reported that the video could not be posted')
        await asyncio.sleep(2)
    else:
        raise RuntimeError('TikTok did not acknowledge the post; the profile will be checked, nothing is re-sent')
    await snap(page,'posted')


ROW_TEXT="""(id)=>{const a=document.querySelector('a[href*="/video/'+id+'"]');if(!a)return null;
    let n=a;while(n.parentElement&&n.parentElement.querySelectorAll('a[href*="/video/"]').length===1)n=n.parentElement;
    return n.innerText}"""
PUBLIC=re.compile(r'(Everyone|Все)')


async def verify_id(context,state,video_id):
    """Read the post back from the signed-in TikTok Studio list: a public video page answers HTTP 403 to
    this server's VPN address (bot protection), so it cannot be the proof. The row must carry the
    saved id, the complete caption and public visibility."""
    page=await context.new_page()
    try:
        await page.goto(SITE+'/tiktokstudio/content',wait_until='domcontentloaded',timeout=60000)
        await page.wait_for_timeout(8000)
        row=await page.evaluate(ROW_TEXT,video_id)
        if row and fmt.caption_visible(row,state['caption']) and PUBLIC.search(row):
            return tt.video_url(state['username'],video_id)
        return None
    except Exception as exc:
        state['verify_error']=type(exc).__name__+': '+str(exc)[:200]
        return None
    finally:await page.close()


async def locate_and_verify(context,page,state,path):
    deadline=time.monotonic()+240
    while True:
        found=tt.new_ids(state.get('codes_before',[]),await grid_ids(page,state['username']))
        for video_id in found[:3]:
            url=await verify_id(context,state,video_id)
            if url:
                state.update(code=video_id,url=url);save(path,state);return
        if time.monotonic()>deadline:
            raise RuntimeError('Posted TikTok video is not visible on the profile yet; no replacement will be sent')
        await asyncio.sleep(20)


async def anonymous_check(browser,state):
    context=await browser.new_context()
    try:
        page=await context.new_page()
        await page.goto(state['url'],wait_until='domcontentloaded',timeout=60000)
        await page.wait_for_timeout(6000)
        visible=re.sub(r'\s+',' ',state['caption']).strip()[:60] in re.sub(r'\s+',' ',await page.locator('body').inner_text())
        state.update(anonymous_checked_at=time.time(),anonymous_visible=visible)
    except Exception as exc:
        state.update(anonymous_checked_at=time.time(),anonymous_visible=False,anonymous_error=type(exc).__name__)
    finally:await context.close()


async def publish(job_path,read_only=False,dry_run=False):
    job_path=Path(job_path).resolve()
    if ROOT not in job_path.parents:raise ValueError('TikTok job must be inside media output')
    job=json.loads(job_path.read_text())
    handle=job['username']
    if not re.fullmatch(r'[A-Za-z0-9_.]{1,24}',handle):raise ValueError('Invalid own-account target')
    path=None if dry_run else ROOT/('native_tiktok_'+job['key']+'.json')
    raw=Path(job['video']).resolve()
    if ROOT not in raw.parents or hashlib.sha256(raw.read_bytes()).hexdigest()!=job['sha256']:
        raise RuntimeError('Reviewed TikTok media file changed')
    width,height,seconds=geometry(raw)
    problem=fmt.reel_problem(width,height,seconds)
    if problem:raise RuntimeError(problem)
    state=json.loads(path.read_text()) if path and path.exists() else {
        'status':'new','username':handle,'source_id':job['source_id'],'caption':job['caption'],
        'sha256':job['sha256'],'media_kind':'video','ai_label':bool(job.get('ai_label')),'started_at':time.time()}
    if (state['username'],state['source_id'],state['sha256'],state['caption'])!=(handle,job['source_id'],job['sha256'],job['caption']):
        raise RuntimeError('TikTok receipt target, source or caption mismatch')
    if state['status']=='published':return state
    if not dry_run:
        for other in ROOT.glob('native_tiktok_*.json'):
            if other!=path and re.fullmatch(r'native_tiktok_[a-f0-9]{64}\.json',other.name):
                prior=json.loads(other.read_text())
                if prior.get('username')==handle and fmt.blocks_new_submission(prior):
                    raise RuntimeError('Another TikTok submission for this account awaits readback')
    with (ROOT/'.flow-generation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        async with async_playwright() as p:
            browser=await connect(p);page=None;context=None;isolated=False
            try:
                context,isolated=await account_context(browser,handle)
                page=await context.new_page()
                await ensure_signed_in(page,handle)
                if state['status']=='share_failed_visible' and not state.get('url'):
                    try:await locate_and_verify(context,page,state,path)
                    except RuntimeError:
                        failed=state.setdefault('failed_shares',[])
                        failed.append(state.pop('share_attempted_at'));state['status']='new'
                        if len(failed)>2:raise RuntimeError('TikTok failed to post three times; the owner must look') from None
                if not state.get('url') and fmt.may_resubmit(state):
                    if read_only:raise RuntimeError('Read-only reconciliation cannot create a post')
                    state['codes_before']=await grid_ids(page,handle);save(path,state)
                    await compose(page,state,path,raw,dry_run)
                    if dry_run:
                        state['status']='dry_run_ok';return state
                if not state.get('url'):await locate_and_verify(context,page,state,path)
                await anonymous_check(browser,state)
                state.update(status='published',published_at=state.get('share_attempted_at') or time.time(),
                    verified_at=time.time(),verification='Signed-in TikTok Studio row with the saved id, complete caption and public visibility')
                if state.get('anonymous_visible'):state['public_verified_at']=state['anonymous_checked_at']
                state.pop('last_error',None)  # an earlier readback failure is history once verified
                save(path,state)
            except Exception as exc:
                if page:
                    try:
                        state['visible_error_context']=(await page.locator('body').inner_text())[-2000:]
                        await page.screenshot(path=str(ROOT/'tiktok_last_error.png'),timeout=5000)
                    except Exception:pass
                state.update(last_error=type(exc).__name__+': '+str(exc)[:500],readback_attempted_at=time.time())
                save(path,state)
                raise
            finally:
                if page:await page.close()
                if isolated:await close_account_context(context,handle)
                await browser.close()
    return state


if __name__=='__main__':
    job=Path(sys.argv[1]).resolve()
    if ROOT not in job.parents:raise ValueError('TikTok job must be inside media output')
    with job.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=asyncio.run(publish(job,'--read-only' in sys.argv,'--dry-run' in sys.argv))
    print(json.dumps({'status':state['status'],'url':state.get('url'),'public_verified_at':state.get('public_verified_at'),
        'published_at':state.get('published_at')},ensure_ascii=False))
