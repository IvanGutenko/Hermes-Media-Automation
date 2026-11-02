#!/usr/bin/env python3
"""Own-account Instagram photo post / Reel publication with a durable receipt and profile readback.

The share click is the only irreversible step. Once it was attempted the post is never uploaded
again: a retry only looks for it on the profile. `--dry-run` goes through upload and caption and
stops before Share, then discards the draft, so the whole flow can be checked without publishing.
"""
import asyncio
import fcntl
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from playwright.async_api import async_playwright, expect, TimeoutError as PlaywrightTimeout
from browser_session import connect
import instagram_format as fmt

ROOT=Path('/data/output')
SITE='https://www.instagram.com'
LOGIN_FIELDS='input[name=username],input[name=email],input[name=pass],input[name=password]'
LOGIN_NAME=re.compile(r'^(Log in|Войти)$',re.I)
ACK=re.compile(r'(Your (post|reel) has been shared|Ваша публикация опубликована|Ваш клип опубликован|Ваш reels опубликован|опубликован)',re.I)
FAILED=re.compile(r"(couldn.t be shared|couldn.t share|Не удалось поделиться|Не удалось опубликовать|Something went wrong)",re.I)


class NeedsLogin(RuntimeError):
    pass


def save(path,state):
    if path is None:return
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2))
    temp.replace(path)


def geometry(path):
    out=subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height:format=duration',
        '-of','json',str(path)],text=True,timeout=30)
    data=json.loads(out)
    return int(data['streams'][0]['width']),int(data['streams'][0]['height']),float(data['format']['duration'])


async def snap(page,tag):
    """Step screenshots make the first real run easy to review."""
    try:
        folder=ROOT/'instagram_debug';folder.mkdir(exist_ok=True)
        await page.screenshot(path=str(folder/(time.strftime('%H%M%S')+'_'+tag+'.png')),timeout=5000)
    except Exception:pass


SWITCH_ACCOUNTS=re.compile(r'^(Switch accounts|Сменить аккаунт|Переключить аккаунты)$')


async def saved_profile_login(page,username):
    """Instagram sometimes ends the session and shows its "Log into Instagram" page with the saved profiles.
    Clicking a saved profile signs in without a password. Returns False when this account has no saved tile or
    Instagram asks for a password: only then does the owner have to step in."""
    await page.goto(SITE+'/',wait_until='domcontentloaded',timeout=60000)
    await page.wait_for_timeout(4000)
    tile=page.get_by_text(re.compile('^'+re.escape(username)+'$')).first
    if not await page.get_by_text(re.compile(r'^(Log into Instagram|Войти в Instagram)$')).count() or not await tile.count():
        return False
    await tile.click()
    await page.wait_for_timeout(6000)
    if await page.locator('input[type=password]').count():return False
    # "Save your login info?": keep the one-click tile for next time.
    save_login=page.get_by_role('button',name=re.compile(r'^(Save|Сохранить)$'))
    try:
        await save_login.first.click(timeout=8000)
        await page.wait_for_timeout(2500)
    except PlaywrightTimeout:pass
    return True


async def switch_account(page,username):
    """Several own accounts stay signed in in the factory browser; make `username` the active one.

    The "Switch accounts" dialog lists them and switching needs no password. An account that is not
    listed was never signed in here, which only the owner can fix."""
    await page.goto(SITE+'/',wait_until='domcontentloaded',timeout=60000)
    await page.wait_for_timeout(3000)
    if await page.get_by_text(re.compile(r'^(Log into Instagram|Войти в Instagram)$')).count():
        if await saved_profile_login(page,username):return
        raise NeedsLogin('Instagram is not signed in in the factory browser; the owner must log in once')
    await page.locator('svg[aria-label="Settings"]').first.click(timeout=30000)
    await page.get_by_text(SWITCH_ACCOUNTS).first.click(timeout=15000)
    row=page.get_by_role('dialog').last.get_by_text(re.compile('^'+re.escape(username)+'$')).first
    try:await row.wait_for(timeout=15000)
    except PlaywrightTimeout:
        raise NeedsLogin('Instagram account %s is not signed in in the factory browser; the owner must log in once (Switch accounts)'%username) from None
    await row.click()
    await expect(page.get_by_role('dialog')).to_have_count(0,timeout=30000)
    await page.wait_for_timeout(1500)


async def ensure_signed_in(page,username):
    for attempt in (0,1):
        await page.goto(SITE+'/'+username+'/',wait_until='domcontentloaded',timeout=60000)
        own=page.locator('a[href="/accounts/edit/"]').first
        login=page.locator(LOGIN_FIELDS).first
        prompt=page.get_by_role('link',name=LOGIN_NAME).or_(page.get_by_role('button',name=LOGIN_NAME)).first
        try:
            await own.or_(login).or_(prompt).first.wait_for(timeout=25000 if attempt==0 else 45000)
        except PlaywrightTimeout:
            # Somebody else's profile has no edit link: another signed-in account is the active one.
            if attempt:raise RuntimeError('Instagram profile page did not show the signed-in account controls') from None
            await switch_account(page,username);continue
        if await own.count() and await own.is_visible():return
        if (await login.count() and await login.is_visible()) or (await prompt.count() and await prompt.is_visible()):
            if attempt==0 and await saved_profile_login(page,username):continue
            raise NeedsLogin('Instagram is not signed in in the factory browser; the owner must log in once')
        raise RuntimeError('The signed-in Instagram account is not the approved profile')


async def grid_codes(page,username):
    await page.wait_for_timeout(3000)
    hrefs=await page.locator('main a[href*="/p/"], main a[href*="/reel/"]').evaluate_all('(es)=>es.map(e=>e.getAttribute("href"))')
    return fmt.profile_codes(hrefs,username)


async def open_composer(page):
    await page.get_by_role('link',name=re.compile(r'(New post|Create|Новая публикация|Создать)',re.I)).first.click(timeout=20000)
    entry=page.get_by_role('link',name=re.compile(r'^(Post|Публикация)\b')).or_(page.get_by_role('menuitem',name=re.compile(r'^(Post|Публикация)\b'))).first
    chooser=page.get_by_role('button',name=re.compile(r'(Select from computer|Выбрать на компьютере|Выбрать с компьютера)',re.I)).first
    await entry.or_(chooser).first.wait_for(timeout=20000)
    if not await chooser.is_visible():await entry.click()
    await chooser.wait_for(timeout=20000)
    return chooser


async def upload_media(page,chooser,raw,kind):
    async with page.expect_file_chooser(timeout=20000) as info:
        await chooser.click()
    await (await info.value).set_files(str(raw),timeout=120000)
    if kind=='video':
        # "Video posts are now shared as reels": a one-time notice with an OK button.
        try:await page.get_by_role('button',name=re.compile(r'^(OK|ОК)$')).first.click(timeout=10000)
        except PlaywrightTimeout:pass


CROP_BUTTON=re.compile(r'crop|обрез|кадр',re.I)
FRAME_WAIT_SECONDS=60
# Visible size of the largest preview (video, image, canvas or blob background) in the composer:
# the media rectangle cut by every clipping ancestor, so a 9:16 video inside a square frame reports a square.
FRAME_JS='''d=>{let best=null;
 for(const e of d.querySelectorAll('video,img,canvas,div')){
  const r=e.getBoundingClientRect();if(r.width<200||r.height<150)continue;
  if(e.tagName==='DIV'&&!/blob:/.test(getComputedStyle(e).backgroundImage))continue;
  let v={l:r.left,t:r.top,r:r.right,b:r.bottom};
  for(let p=e.parentElement;p;p=p.parentElement){
   const s=getComputedStyle(p);
   if(/(hidden|clip|auto|scroll)/.test(s.overflow+s.overflowX+s.overflowY)){const q=p.getBoundingClientRect();
    v={l:Math.max(v.l,q.left),t:Math.max(v.t,q.top),r:Math.min(v.r,q.right),b:Math.min(v.b,q.bottom)}}
   if(p===d)break}
  const w=v.r-v.l,h=v.b-v.t;if(w<=0||h<=0)continue;
  if(!best||w*h>best[0]*best[1])best=[Math.round(w),Math.round(h)]}
 return best}'''


async def frame_window(page):
    try:return await page.get_by_role('dialog').last.evaluate(FRAME_JS)
    except Exception:return None


async def settle_frame(page,size,kind,waited):
    """Make the crop step show the whole media. Returns False while the preview is still loading.

    The web composer opens on a square 1:1 crop; a Reel is chosen as 9:16 and a photo as Original.
    Past the crop step a cropped preview is never shared."""
    crop=page.get_by_role('button',name=CROP_BUTTON)
    window=await frame_window(page)
    if window is None and await crop.count():
        if waited>FRAME_WAIT_SECONDS:raise RuntimeError('Instagram crop preview could not be measured; nothing was posted')
        return False
    problem=fmt.frame_problem(window,size) if window else None
    if not problem:return True
    if not await crop.count():raise RuntimeError(problem)
    await crop.first.click()
    await page.get_by_text(re.compile('^'+('9:16' if kind=='video' else 'Original')+'$')).first.click()
    await page.wait_for_timeout(1500)
    problem=fmt.frame_problem(await frame_window(page),size)
    if problem:raise RuntimeError(problem)
    return True


async def to_caption_step(page,box,size=None,kind='video'):
    """Click through crop/edit steps until the caption box appears. Instagram sometimes answers a photo
    with "Something went wrong": retry once, then fail quickly instead of holding the browser for minutes.
    With `size`, no step is left while the preview crops the media."""
    nxt=page.get_by_role('button',name=re.compile(r'^(Next|Далее)$'))
    retry=page.get_by_role('button',name=re.compile(r'^(Try again|Повторить)$'))
    problem=page.get_by_text(re.compile(r'^(Something went wrong|Что-то пошло не так)',re.I))
    deadline=time.monotonic()+240;clicks=0;retried=False;started=time.monotonic()
    while time.monotonic()<deadline:
        if await box.is_visible():
            window=await frame_window(page) if size else None
            problem=fmt.frame_problem(window,size) if window else None
            if problem:raise RuntimeError(problem)
            return
        if await problem.count() and await problem.first.is_visible():
            await snap(page,'something_went_wrong')
            if retried or not await retry.count():
                raise RuntimeError('Instagram reported "Something went wrong" while preparing the upload')
            retried=True;deadline=min(deadline,time.monotonic()+60)
            await retry.first.click();await page.wait_for_timeout(3000);continue
        if await nxt.count() and await nxt.first.is_visible():
            if clicks>=5:break
            if size and not await settle_frame(page,size,kind,time.monotonic()-started):
                await asyncio.sleep(1);continue
            await snap(page,'before_next')
            await nxt.first.click();clicks+=1;await page.wait_for_timeout(1500);continue
        await asyncio.sleep(1)
    raise RuntimeError('Instagram composer did not reach the caption step')


async def discard(page):
    """Close the composer without leaving a draft."""
    try:
        await page.get_by_role('button',name=re.compile(r'^(Close|Закрыть)$')).first.click(timeout=5000)
    except Exception:
        await page.keyboard.press('Escape')
    try:await page.get_by_role('button',name=re.compile(r'^(Discard|Отменить|Удалить|Не сохранять)$')).first.click(timeout=5000)
    except Exception:pass


async def compose(page,state,path,raw,dry_run):
    chooser=await open_composer(page)
    await snap(page,'composer')
    state.update(status='uploading',upload_attempted_at=time.time());save(path,state)
    await upload_media(page,chooser,raw,state['media_kind'])
    box=page.get_by_role('textbox',name=re.compile(r'(Write a caption|Добавьте подпись|Напишите подпись|Caption|Подпись)',re.I)).first
    await to_caption_step(page,box,geometry(raw)[:2],state['media_kind'])
    await box.click();await box.fill(state['caption'])
    if not fmt.caption_visible(await box.inner_text(),state['caption']):
        raise RuntimeError('Instagram composer did not retain the reviewed caption')
    if state.get('ai_label'):
        # Instagram requires this label for realistic media made with AI; the factory's clips and
        # illustrations are generated.
        # The switch is an unnamed input[role=switch]; its label is a sibling span.
        toggle=page.locator('xpath=//span[contains(normalize-space(),"AI label") or contains(normalize-space(),"метку ИИ")]'
            '/ancestor::div[.//input[@role="switch"]][1]//input[@role="switch"]').first
        if not await toggle.is_checked():await toggle.click()
        await expect(toggle).to_be_checked(timeout=10000)
    await snap(page,'caption')
    share=page.get_by_role('button',name=re.compile(r'^(Share|Поделиться)$')).last
    await expect(share).to_be_enabled(timeout=60000)
    if dry_run:
        await discard(page);await snap(page,'after_discard');return
    state.update(status='publishing',share_attempted_at=time.time());save(path,state)
    await share.click()
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        if await page.get_by_text(ACK).first.is_visible():
            state.update(status='posted_unverified',share_acknowledged_at=time.time());save(path,state);break
        if await page.get_by_text(FAILED).first.is_visible():
            state.update(status='share_failed_visible');save(path,state)
            raise RuntimeError('Instagram reported that the post could not be shared')
        await asyncio.sleep(2)
    else:
        raise RuntimeError('Instagram did not acknowledge the share; the profile will be checked, nothing is re-sent')
    await snap(page,'shared')
    await discard(page)


async def verify_code(context,state,code):
    page=await context.new_page()
    try:
        await page.goto(fmt.permalink(code,state['media_kind']),wait_until='domcontentloaded',timeout=60000)
        await page.locator('a[href="/'+state['username']+'/"]').first.wait_for(timeout=30000)
        more=page.get_by_role('button',name=re.compile(r'^(more|ещё)$',re.I))
        if await more.count() and await more.first.is_visible():await more.first.click()
        visible=await page.locator('body').inner_text()
        if not fmt.caption_visible(visible,state['caption']):return None
        return page.url.split('?')[0]
    finally:await page.close()


async def locate_and_verify(context,page,state,path):
    """Find the shared post as a new profile entry whose author and complete caption match."""
    deadline=time.monotonic()+150
    while True:
        await page.goto(SITE+'/'+state['username']+'/',wait_until='domcontentloaded',timeout=60000)
        found=fmt.new_codes(state.get('codes_before',[]),await grid_codes(page,state['username']))
        for code in found[:3]:
            url=await verify_code(context,state,code)
            if url:
                state.update(code=code,url=url);save(path,state);return
        if time.monotonic()>deadline:
            raise RuntimeError('Shared Instagram post is not visible on the profile yet; no replacement will be sent')
        await asyncio.sleep(15)


async def anonymous_check(browser,state):
    """Best effort: a logged-out visitor should see the caption. A login wall is recorded, not fatal."""
    context=await browser.new_context()
    try:
        page=await context.new_page()
        await page.goto(state['url'],wait_until='domcontentloaded',timeout=60000)
        await page.wait_for_timeout(5000)
        meta=await page.locator('meta[property="og:description"]').first.get_attribute('content') if await page.locator('meta[property="og:description"]').count() else ''
        body=await page.locator('body').inner_text()
        probe=re.sub(r'\s+',' ',state['caption']).strip()[:60]
        visible=probe in re.sub(r'\s+',' ',(meta or '')+' '+body)
        state.update(anonymous_checked_at=time.time(),anonymous_visible=visible)
    except Exception as exc:
        state.update(anonymous_checked_at=time.time(),anonymous_visible=False,anonymous_error=type(exc).__name__)
    finally:await context.close()


async def publish(job_path,read_only=False,dry_run=False):
    job_path=Path(job_path).resolve()
    if ROOT not in job_path.parents:raise ValueError('Instagram job must be inside media output')
    job=json.loads(job_path.read_text())
    username=job['username']
    if not re.fullmatch(r'[A-Za-z0-9_.]{1,30}',username):raise ValueError('Invalid own-account target')
    path=None if dry_run else ROOT/('native_instagram_'+job['key']+'.json')
    raw=Path(job['video'] if job.get('video') else job['image']).resolve()
    if ROOT not in raw.parents or hashlib.sha256(raw.read_bytes()).hexdigest()!=job['sha256']:
        raise RuntimeError('Reviewed Instagram media file changed')
    kind='video' if job.get('video') else 'image'
    width,height,seconds=geometry(raw)
    problem=fmt.reel_problem(width,height,seconds) if kind=='video' else (
        'Image does not fit the Instagram feed range; prepare it first' if fmt.photo_target(width,height) else None)
    if problem:raise RuntimeError(problem)
    state=json.loads(path.read_text()) if path and path.exists() else {
        'status':'new','username':username,'source_id':job['source_id'],'caption':job['caption'],
        'sha256':job['sha256'],'media_kind':kind,'ai_label':bool(job.get('ai_label')),'started_at':time.time()}
    if (state['username'],state['source_id'],state['sha256'],state['caption'])!=(username,job['source_id'],job['sha256'],job['caption']):
        raise RuntimeError('Instagram receipt target, source or caption mismatch')
    if state['status']=='published':return state
    if not dry_run:
        for other in ROOT.glob('native_instagram_*.json'):
            if other!=path and re.fullmatch(r'native_instagram_[a-f0-9]{64}\.json',other.name):
                prior=json.loads(other.read_text())
                if prior.get('username')==username and fmt.blocks_new_submission(prior):
                    raise RuntimeError('Another Instagram submission for this account awaits readback')
    with (ROOT/'.flow-generation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        async with async_playwright() as p:
            browser=await connect(p);page=None
            try:
                context=browser.contexts[0]
                page=await context.new_page()
                await ensure_signed_in(page,username)
                if state['status']=='share_failed_visible' and not state.get('url'):
                    # Instagram said it failed; resubmit only if the profile really has no such post.
                    try:await locate_and_verify(context,page,state,path)
                    except RuntimeError:
                        failed=state.setdefault('failed_shares',[])
                        failed.append(state.pop('share_attempted_at'));state['status']='new'
                        if len(failed)>2:raise RuntimeError('Instagram failed to share three times; the owner must look') from None
                if not state.get('url') and fmt.may_resubmit(state):
                    if read_only:raise RuntimeError('Read-only reconciliation cannot create a post')
                    await page.goto(SITE+'/'+username+'/',wait_until='domcontentloaded',timeout=60000)
                    state['codes_before']=await grid_codes(page,username);save(path,state)
                    await compose(page,state,path,raw,dry_run)
                    if dry_run:
                        state['status']='dry_run_ok';return state
                if not state.get('url'):await locate_and_verify(context,page,state,path)
                await anonymous_check(browser,state)
                state.update(status='published',published_at=state.get('share_attempted_at') or time.time(),
                    verified_at=time.time(),verification='Own-profile author and complete caption on the saved permalink')
                if state.get('anonymous_visible'):state['public_verified_at']=state['anonymous_checked_at']
                state.pop('last_error',None)  # an earlier readback failure is history once verified
                save(path,state)
            except Exception as exc:
                if page:
                    try:
                        state['visible_error_context']=(await page.locator('body').inner_text())[-2000:]
                        await page.screenshot(path=str(ROOT/'instagram_last_error.png'),timeout=5000)
                    except Exception:pass
                state.update(last_error=type(exc).__name__+': '+str(exc)[:500],readback_attempted_at=time.time())
                save(path,state)
                raise
            finally:
                if page:await page.close()
                await browser.close()
    return state


if __name__=='__main__':
    job=Path(sys.argv[1]).resolve()
    if ROOT not in job.parents:raise ValueError('Instagram job must be inside media output')
    with job.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=asyncio.run(publish(job,'--read-only' in sys.argv,'--dry-run' in sys.argv))
    print(json.dumps({'status':state['status'],'url':state.get('url'),'public_verified_at':state.get('public_verified_at'),
        'published_at':state.get('published_at')},ensure_ascii=False))
