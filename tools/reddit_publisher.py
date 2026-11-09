#!/usr/bin/env python3
"""Explicit-destination Reddit publication with durable ids and public readback.

A created id is an acknowledgement, not proof of public publication. An uncertain
submission is reconciled on the same id and blocks replacement submissions.
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
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright, expect
from browser_session import connect
from reddit_format import platform_body
from platform_policy import DISABLED_PLATFORMS,disabled_result
from reddit_targets import destination,home,permalink,same,matches_community

ROOT=Path('/data/output')


class PlatformRejected(RuntimeError):
    pass


UNCERTAIN_BLOCK_SECONDS=6*3600


def blocks_new_submission(state,now=None):
    """An uncertain earlier submission blocks new ones for six hours, not forever: on 2026-11-01 one lost
    post id kept every newer Reddit post waiting. The uncertain source itself is still never resent."""
    now=now or time.time()
    attempted=max((state.get(k) or 0 for k in ('publication_attempted_at','old_submission_attempted_at')),default=0)
    if attempted and now-attempted>UNCERTAIN_BLOCK_SECONDS:return False
    return (state.get('status') not in ('new','published','cancelled_before_submission','rejected_by_platform','retired_rejected','abandoned_unverified')
        and any(state.get(k) for k in ('id','publication_attempted_at','old_submission_attempted_at')))


def save(path,state):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2))
    temp.replace(path)


def post_id(url):
    parsed=urlparse(url)
    if parsed.hostname not in ('www.reddit.com','reddit.com','old.reddit.com'):return None
    created=parse_qs(parsed.query).get('created',[''])[0]
    match=re.fullmatch(r't3_([a-z0-9]+)',created) or re.search(r'/comments/([a-z0-9]+)/?',parsed.path)
    return match.group(1) if match else None


def normalized(text):return ' '.join(text.split())


def source_size(path):
    out=subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height',
        '-of','csv=p=0',str(path)],text=True,timeout=20)
    width,height=out.strip().split(',')[:2]
    return int(width),int(height)


def min_loaded_size(source):
    """Reddit inserts an image at its own size. A large source must arrive at least 600x400; a small
    one, such as a tweet screenshot of 524x800, must arrive at its full size. The old fixed floor
    rejected correctly loaded small images and left a post stuck."""
    return min(source[0],600),min(source[1],400)


def loaded_at_source_size(inserted,source):
    need=min_loaded_size(source)
    return inserted['width']>=need[0] and inserted['height']>=need[1]


def matches_uploaded_image(photo,upload):
    parsed=urlparse(photo.get('src',''))
    identity=upload.get('media_id')
    return bool(identity and parsed.hostname in ('preview.redd.it','i.redd.it')
        and re.search(r'(?:^|[-/])'+re.escape(identity)+r'\.',parsed.path)
        and photo.get('complete') and photo.get('width',0)>0 and photo.get('height',0)>0
        and photo.get('display_width',0)>100 and photo.get('display_height',0)>100)


async def discover(context,state):
    """Locate a manually completed post in its original destination without submitting anything."""
    page=await context.new_page()
    try:
        listing=home(state,old=True)+('/new/' if destination(state).startswith('r_') else '/submitted/?sort=new')
        await page.goto(listing,wait_until='domcontentloaded',timeout=30000)
        posts=page.locator('.thing[data-fullname^="t3_"]')
        for i in range(await posts.count()):
            post=posts.nth(i)
            if not await post.locator('a.title').count():continue
            if await post.locator('a.title').inner_text()!=state['title']:continue
            if (await post.locator('a.author').first.inner_text()).casefold()!=state['username'].casefold():continue
            if not matches_community(destination(state),await post.get_attribute('data-subreddit')):continue
            identity=(await post.get_attribute('data-fullname')).removeprefix('t3_')
            state.update(id=identity,url=permalink(state,identity,old=True),interface='old')
            return
        raise RuntimeError('Reviewed post is not present in the own profile; awaiting platform/manual completion')
    finally:await page.close()


async def readback(context,state):
    page=await context.new_page()
    try:
        if not state.get('media_present') and state.get('interface')=='old':
            await page.goto(permalink(state,state['id'],old=True),wait_until='domcontentloaded',timeout=30000)
            post=page.locator('.thing[data-fullname="t3_'+state['id']+'"]')
            await post.wait_for(timeout=20000)
            if not matches_community(destination(state),await post.get_attribute('data-subreddit')):
                raise RuntimeError('Reddit text post belongs to a different community')
            if (await post.locator('a.author').first.inner_text()).casefold()!=state['username'].casefold():
                raise RuntimeError('Reddit text post belongs to another author')
            if await post.locator('a.title').inner_text()!=state['title']:
                raise RuntimeError('Reddit text post title differs from reviewed title')
            if normalized(await post.locator('.usertext-body').first.inner_text())!=normalized(state['body']):
                raise RuntimeError('Reddit text post body differs from reviewed text')
            return {'url':page.url,'verified_at':time.time()}
        await page.goto(state['url'],wait_until='domcontentloaded',timeout=30000)
        missing=page.get_by_text(re.compile(r'^(Страница не найдена|Page not found)$'),exact=True)
        post=page.locator('shreddit-post[id="t3_'+state['id']+'"]')
        await post.or_(missing).first.wait_for(timeout=20000)
        if await missing.is_visible():raise RuntimeError('Reddit shows Page not found for the saved post id')
        await post.wait_for(timeout=20000)
        community=await post.get_attribute('subreddit-prefixed-name') or await post.get_attribute('subreddit-name')
        if not matches_community(destination(state),community):
            raise RuntimeError('Reddit post belongs to a different community')
        if (await post.get_attribute('author') or '').casefold()!=state['username'].casefold():
            raise RuntimeError('Reddit post owner differs from the approved profile')
        if await post.get_attribute('post-title')=='[ Removed by Reddit ]':
            visible=await post.inner_text()
            state.update(platform_rejection=visible[:1500],platform_rejection_observed_at=time.time())
            raise PlatformRejected('Reddit removed this post: '+('URL not allowed' if 'Links to a URL not allowed on Reddit' in visible else 'platform content policy'))
        if await post.get_attribute('post-title')!=state['title']:
            raise RuntimeError('Reddit post title differs from the reviewed title')
        for label in ('Читать дальше','Read more'):
            more=post.get_by_text(label,exact=True)
            if await more.count()==1 and await more.is_visible():await more.click()
        visible=normalized(await post.inner_text())
        if any(normalized(p) not in visible for p in state['body'].split('\n\n') if p.strip()):
            raise RuntimeError('Reddit post body differs from the reviewed text')
        if state.get('media_kind')=='image':
            deadline=time.monotonic()+20
            while True:
                candidates=await post.locator('figure.rte-media img').evaluate_all('''(es)=>es.map(e=>({
                    src:e.currentSrc || e.src,complete:e.complete,width:e.naturalWidth,height:e.naturalHeight,
                    display_width:e.getBoundingClientRect().width,display_height:e.getBoundingClientRect().height}))''')
                photos=[photo for photo in candidates if matches_uploaded_image(photo,state.get('image_upload',{}))]
                if photos:break
                if time.monotonic()>deadline:raise RuntimeError('Reddit post image is not visible yet')
                await asyncio.sleep(1)
            state['photo_readback']=photos
        elif state.get('media_present'):
            player=post.locator('shreddit-player,video').first
            await player.wait_for(timeout=20000)
            if await player.get_attribute('player-type')=='video_preview':
                raise RuntimeError('Reddit media is still a composer preview')
        return {'url':page.url,'verified_at':time.time()}
    finally:await page.close()


async def publish(job_path,read_only=False):
    if 'reddit' in DISABLED_PLATFORMS:return disabled_result('reddit')
    job_path=Path(job_path).resolve()
    if ROOT not in job_path.parents:raise ValueError('Reddit job must be inside media output')
    job=json.loads(job_path.read_text())
    username=job['username']
    target=destination(job)
    if not re.fullmatch(r'[A-Za-z0-9_-]{3,20}',username):raise ValueError('Invalid own-profile target')
    path=ROOT/('native_reddit_'+job['key']+'.json')
    state=json.loads(path.read_text()) if path.exists() else {
        'status':'new','username':username,'target':target,'source_id':job['source_id'],
        'title':job['title'],'body':job['body'],'sha256':job.get('sha256'),
        'media_present':bool(job.get('video') or job.get('image')),
        'media_kind':'video' if job.get('video') else ('image' if job.get('image') else 'text'),'started_at':time.time()}
    if (state['username']!=username or not same(destination(state),target)
            or state['source_id']!=job['source_id'] or state.get('sha256')!=job.get('sha256')):
        raise RuntimeError('Reddit receipt target or source mismatch')
    if state['status']=='published':return state
    if state['status']=='rejected_by_platform':raise PlatformRejected(state.get('last_error','Reddit rejected this post; no replacement sent'))
    never_submitted=not any(state.get(k) for k in ('id','publication_attempted_at','old_submission_attempted_at'))
    if not read_only and never_submitted and state['body']!=platform_body(state['body']):
        state.update(original_body=state['body'],body=platform_body(state['body']),body_format_version=1)
        save(path,state)
    raw=Path(job.get('video') or job['image']).resolve() if job.get('video') or job.get('image') else None
    if raw and (ROOT not in raw.parents or hashlib.sha256(raw.read_bytes()).hexdigest()!=job['sha256']):
        raise RuntimeError('Reviewed Reddit media file changed')
    with (ROOT/'.flow-generation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        async with async_playwright() as p:
            browser=await connect(p);page=None
            try:
                context=browser.contexts[0]
                if read_only and not state.get('id'):
                    await discover(context,state);save(path,state)
                if not state.get('id'):
                    old_retry=(target.startswith('u_') and not raw and state['status']=='rejected_target_unavailable' and not state.get('old_submission_attempted_at'))
                    upload_retry=raw and state['status']=='uploading' and never_submitted
                    if state['status']!='new' and not old_retry and not upload_retry:
                        raise RuntimeError('Reddit submission is uncertain; no replacement will be sent')
                    # A pending upload blocks further uploads of the same kind.
                    # Text posts are independently verifiable when video processing
                    # has failed; they retain their own source and id receipts.
                    for previous in ROOT.glob('native_reddit_*.json'):
                        if not re.fullmatch(r'native_reddit_[a-f0-9]{64}\.json',previous.name):continue
                        other=json.loads(previous.read_text())
                        if previous!=path and (other.get('username')==username and same(destination(other),target) and bool(other.get('media_present'))==bool(raw)
                                and blocks_new_submission(other)):
                            raise RuntimeError('Another Reddit submission for this profile awaits readback')
                    if read_only:raise RuntimeError('Read-only reconciliation cannot create a post')
                    page=await context.new_page()
                    if not raw and old_retry:
                        await page.goto('https://old.reddit.com/user/'+username+'/submit?selftext=true',wait_until='domcontentloaded',timeout=30000)
                        await page.get_by_text('u/'+username,exact=True).first.wait_for(timeout=20000)
                        await page.locator('textarea[name=title]').fill(state['title'])
                        await page.locator('textarea[name=text]').fill(state['body'])
                        await page.locator('input[name=submit_type][value=profile]').check()
                        if (await page.locator('textarea[name=title]').input_value()!=state['title']
                                or await page.locator('textarea[name=text]').input_value()!=state['body']
                                or not await page.locator('input[name=submit_type][value=profile]').is_checked()):
                            raise RuntimeError('Old Reddit form changed the reviewed post or own-profile target')
                        state.update(status='publishing',interface='old',old_submission_attempted_at=time.time());save(path,state)
                        await page.get_by_role('button',name='submit',exact=True).click()
                        deadline=time.monotonic()+60
                        while not post_id(page.url) and time.monotonic()<deadline:
                            visible=await page.locator('.error').evaluate_all('(es)=>es.filter(e=>e.innerText.trim()).map(e=>({text:e.innerText,className:e.className}))')
                            if visible:
                                state.update(status='needs_platform_verification',platform_errors=visible)
                                save(path,state)
                                raise RuntimeError('Old Reddit rejected submission: '+visible[0]['text'])
                            await asyncio.sleep(1)
                        if not post_id(page.url):raise RuntimeError('Old Reddit submission is uncertain; no repeat sent')
                        identity=post_id(page.url)
                        state.update(id=identity,url=page.url);save(path,state)
                    else:
                        await submit_video(page,state,path,raw,username,media_kind=state.get('media_kind','video'))

                await readback(context,state)
                anonymous=await browser.new_context()
                try:proof=await readback(anonymous,state)
                finally:await anonymous.close()
                state.update(status='published',url=proof['url'],public_verified_at=proof['verified_at'],
                    verification='Exact title, complete body, approved author, destination '+target+' and attached media in signed-in and anonymous browser')
                save(path,state)
            except Exception as exc:
                if isinstance(exc,PlatformRejected):
                    state.update(status='rejected_by_platform',terminal_failure_observed=True)
                if page:
                    try:
                        state['visible_error_context']=(await page.locator('body').inner_text())[-2500:]
                        await page.screenshot(path=str(path.with_suffix('.png')),full_page=False,timeout=5000)
                    except Exception:pass
                state.update(last_error=type(exc).__name__+': '+str(exc)[:500],readback_attempted_at=time.time())
                save(path,state)
                raise
            finally:
                if page:await page.close()
                await browser.close()
    return state


async def submit_video(page,state,path,raw,username,media_kind='video'):
    await page.goto('https://www.reddit.com/user/'+username+'/submitted/',wait_until='domcontentloaded',timeout=30000)
    await expect(page.get_by_role('heading',name=username,exact=True).first).to_be_visible(timeout=20000)
    await page.goto(home(state)+'/submit',wait_until='domcontentloaded',timeout=30000)
    if destination(state).startswith('r_'):
        await page.get_by_role('button',name='r/'+destination(state)[2:],exact=True).wait_for(timeout=20000)
    title=page.get_by_role('textbox',name=re.compile(r'^(Заголовок|Title)$'))
    body=page.locator('[contenteditable=true][name=body]')
    await title.fill(state['title']);await body.fill(state['body'])
    if raw:
        state.update(status='uploading',upload_attempted_at=time.time());save(path,state)
        async with page.expect_file_chooser() as chooser_info:
            await page.get_by_role('button',name=re.compile(r'^(Изображение|Image)$') if media_kind=='image' else re.compile(r'^(Видео|Video)$')).click()
        await (await chooser_info.value).set_files(str(raw),timeout=60000)
        if media_kind=='image':
            # The carousel includes an empty selection thumbnail (index -1).
            # Reddit requires its explicit insertion action before the uploaded
            # photo becomes part of this rich-text post.
            insert=page.get_by_role('button',name=re.compile(
                r'^(Добавить в основной текст Добавить медиа в основной текст|Add to body Add media to body)$'))
            await expect(insert).to_be_enabled(timeout=180000)
            await insert.click()
            media=body.locator('rte-image[media-id]')
            image=media.locator('img')
            await expect(image).to_have_attribute('src',re.compile(r'.+'),timeout=30000)
            await expect(image).to_have_js_property('complete',True,timeout=30000)
            dimensions=await image.evaluate('(e)=>({width:e.naturalWidth,height:e.naturalHeight})')
            if not loaded_at_source_size(dimensions,source_size(raw)):
                raise RuntimeError('Reddit inserted image did not load at the reviewed resolution')
            state.update(image_upload={'media_id':await media.get_attribute('media-id'),**dimensions})
            save(path,state)
        else:await page.locator('shreddit-player[player-type=video_preview]').wait_for(timeout=180000)
    if await title.input_value()!=state['title'] or normalized(await body.inner_text())!=normalized(state['body']):
        raise RuntimeError('Reddit composer did not retain the reviewed text')
    if destination(state).startswith('r_'):
        await expect(page.get_by_role('button',name='r/'+destination(state)[2:],exact=True)).to_be_visible()
        if urlparse(page.url).path.rstrip('/').casefold()!=('/r/'+destination(state)[2:]+'/submit').casefold():
            raise RuntimeError('Reddit composer changed the approved community')
    button=page.get_by_role('button',name=re.compile(r'^(Опубликовать|Post)$'),exact=True)
    await expect(button).to_be_enabled(timeout=180000)
    def navigation(frame):
        if frame!=page.main_frame:return
        identity=post_id(frame.url)
        if identity:
            state.update(id=identity,url=permalink(state,identity),last_url=frame.url)
            save(path,state)
    page.on('framenavigated',navigation)
    state.update(status='publishing',publication_attempted_at=time.time());save(path,state)
    await button.click()
    deadline=time.monotonic()+60
    while not state.get('id') and time.monotonic()<deadline:
        error=page.get_by_text('Это сообщество не существует. Попробуйте проверить правильность написания.',exact=True)
        if await error.is_visible():
            state.update(status='rejected_target_unavailable',platform_error=await error.inner_text(),last_url=page.url)
            save(path,state)
            raise RuntimeError('Reddit rejects the selected own-profile community as nonexistent')
        navigation(page.main_frame)
        if state.get('id'):break
        await asyncio.sleep(1)
    navigation(page.main_frame)
    if not state.get('id'):raise RuntimeError('Reddit did not return a post id; no replacement will be sent')

if __name__=='__main__':
    job=Path(sys.argv[1]).resolve()
    if ROOT not in job.parents:raise ValueError('Reddit job must be inside media output')
    with job.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=asyncio.run(publish(job,'--read-only' in sys.argv))
    print(json.dumps({'status':state['status'],'url':state.get('url')},ensure_ascii=False))
