#!/usr/bin/env python3
"""Publish a reviewed vertical MP4 as a native community VK Clip, with durable ids."""
import asyncio
import fcntl
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from playwright.async_api import async_playwright,expect
from browser_session import connect

ROOT=Path('/data/output')


def save(path,state):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2));temp.replace(path)


def normalized(text):return re.sub(r'\s+',' ',text).strip()


async def dismiss_intro(page):
    for name in ['onboarding_close_btn','video-publish-clip-from-video-modal-continue-button']:
        button=page.get_by_test_id(name)
        if await button.is_visible():await button.click()


async def readback(ctx,state):
    page=await ctx.new_page()
    try:
        await page.goto('https://vk.ru/clip'+str(state['video_owner_id'])+'_'+str(state['video_id']),wait_until='domcontentloaded',timeout=60000)
        await expect(page.locator('body')).to_contain_text(state['job']['title'],timeout=60000)
        body=normalized(await page.locator('body').inner_text())
        if normalized(state['caption']) not in body or state['job']['group_title'] not in body:
            more=page.get_by_text('Ещё',exact=True)
            if await more.count()==1 and await more.is_visible():await more.click()
            body=normalized(await page.locator('body').inner_text())
        if normalized(state['caption']) not in body or state['job']['group_title'] not in body:
            raise RuntimeError('VK clip has a different caption or community')
        state.update(status='published',url=page.url,verified_at=time.time(),
                     verification_reason='Full exact caption and community visible on the saved clip URL')
    finally:await page.close()


async def upload(job):
    raw=Path(job['video']).resolve();group=int(job['group_id'])
    if ROOT not in raw.parents:raise ValueError('Video must be inside media output')
    sha=hashlib.sha256(raw.read_bytes()).hexdigest()
    if sha!=job['sha256']:raise RuntimeError('Video changed after review')
    path=ROOT/f'native_vk_{group}_{sha[:16]}.json'
    state=json.loads(path.read_text()) if path.exists() else {
        'status':'new','job':job,'target_group':group,'sha256':sha,'marker':'media_'+sha[:16],'started_at':time.time()}
    if state['sha256']!=sha or state['target_group']!=group:raise RuntimeError('Receipt target or file mismatch')
    caption=job['title']+'\n\n'+job['description']
    if state.get('caption') and state['caption']!=caption:raise RuntimeError('Caption changed after upload')
    state['job'].setdefault('group_title',job['group_title'])
    state['caption']=caption;save(path,state)
    with (ROOT/'.flow-generation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        async with async_playwright() as p:
            browser=await connect(p);page=None
            try:
                ctx=browser.contexts[0]
                if state.get('video_id') and state['status'] in ('published','publishing','posted_unverified'):
                    try:
                        await readback(ctx,state);save(path,state);return state
                    except Exception:
                        if state['status']=='published':raise
                page=next((x for x in ctx.pages if f'cabinet.vkvideo.ru/dashboard/@club{group}' in x.url),None)
                if state['status']=='new':
                    if page is None:
                        page=await ctx.new_page()
                        await page.goto(f'https://cabinet.vkvideo.ru/dashboard/@club{group}',wait_until='domcontentloaded',timeout=60000)
                    await page.get_by_role('button',name=re.compile('^'+re.escape(job['group_title']))).wait_for(timeout=60000)
                    await dismiss_intro(page)
                    await page.keyboard.press('Escape')
                    await page.get_by_role('button',name='Добавить',exact=True).click()
                    await page.get_by_text('Загрузить видео',exact=True).last.click()
                    await page.locator('input[type=file]').wait_for(state='attached')
                    state.update(status='uploading',upload_attempted_at=time.time());save(path,state)
                    await page.locator('input[type=file]').set_input_files({
                        'name':state['marker']+'.mp4','mimeType':'video/mp4','buffer':raw.read_bytes()},timeout=60000)
                elif page is None:
                    # A lost upload may be a private draft. Never blindly create another.
                    raise RuntimeError('Existing VK upload needs draft reconciliation; no replacement upload sent')
                form=page.get_by_test_id('video_upload_clip_form')
                await form.wait_for(timeout=180000)
                await dismiss_intro(page)
                body=await form.inner_text()
                match=re.search(r'https://vkvideo.ru/clip(-?\d+)_(\d+)',body)
                if not match:
                    await expect(form).to_contain_text('https://vkvideo.ru/clip',timeout=180000)
                    match=re.search(r'https://vkvideo.ru/clip(-?\d+)_(\d+)',await form.inner_text())
                if not match or int(match[1])!=-group:raise RuntimeError('VK clip belongs to another community')
                if state.get('video_id') and int(match[2])!=state['video_id']:raise RuntimeError('VK form belongs to another upload')
                if state['marker']+'.mp4' not in await form.inner_text():raise RuntimeError('VK form has a different file marker')
                state.update(video_owner_id=int(match[1]),video_id=int(match[2]),url=match[0],status='draft_processing');save(path,state)
                field=page.get_by_placeholder('О чём ваш клип?');await field.fill(caption)
                if await field.input_value()!=caption:raise RuntimeError('VK caption did not persist')
                # Story publication is separate and unnecessary for the clip schedule.
                for pattern in ['Опубликовать в.*истории','Разрешить дуэты']:
                    label=page.locator('label').filter(has_text=re.compile(pattern))
                    if await label.count()==1:
                        checkbox=label.locator('input[type=checkbox]')
                        if await checkbox.count() and await checkbox.is_checked():
                            await label.click()
                            await expect(checkbox).not_to_be_checked()
                button=page.get_by_role('button',name='Опубликовать',exact=True)
                await expect(button).to_be_enabled(timeout=180000)
                state.update(status='publishing',publication_attempted_at=time.time());save(path,state)
                await button.click()
                await expect(page.locator('body')).to_contain_text('Клип опубликован',timeout=60000)
                state.update(status='posted_unverified',publication_ack='Клип опубликован');save(path,state)
                await readback(ctx,state);save(path,state)
            finally:
                if page and state.get('status')=='published':await page.close()
                await browser.close()
    return state


if __name__=='__main__':
    job=json.loads(Path(sys.argv[1]).read_text())
    result=asyncio.run(upload(job))
    print(json.dumps({k:result[k] for k in ['status','url','video_id','video_owner_id','sha256','verified_at','verification_reason']},ensure_ascii=False))
