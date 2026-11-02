#!/usr/bin/env python3
"""Generate one Russian narration in the owner's included Vids access, with a receipt."""
import asyncio
import fcntl
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from playwright.async_api import async_playwright, expect
from browser_session import connect

ROOT=Path('/data/output')


def save(path,data):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2));temp.chmod(0o644);temp.replace(path)


async def generate(run_id,script,voice='Holt'):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',run_id) or not re.fullmatch(r'[А-Яа-яЁё ]+',script):
        raise ValueError('A run id and Russian letters-only narration are required')
    receipt=ROOT/('vids_voice_'+run_id+'.json');target=receipt.with_suffix('.mp4')
    if not re.fullmatch(r'[A-Za-z ]{2,30}',voice):raise ValueError('Invalid Vids voice name')
    state=json.loads(receipt.read_text()) if receipt.exists() else {'run_id':run_id,'script':script,'voice':voice,'status':'new','started_at':time.time()}
    if state['script']!=script or state['voice']!=voice:raise RuntimeError('Vids receipt belongs to another script or voice')
    if state['status']=='completed':
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=state['sha256']:raise RuntimeError('Vids file differs from receipt')
        return state
    with (ROOT/'.flow-generation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        async with async_playwright() as p:
            browser=await connect(p)
            try:
                ctx=browser.contexts[0]
                page=next((x for x in ctx.pages if state.get('document_url') and state['document_url'].split('?')[0] in x.url),None)
                if page is None:
                    page=await ctx.new_page()
                    await page.goto(state.get('document_url','https://docs.google.com/videos/create?authuser=0'),wait_until='commit',timeout=45000)
                    await page.wait_for_url(re.compile(r'https://docs\.google\.com/videos/d/[^/]+/edit'),wait_until='commit',timeout=60000)
                    if re.match(r'https://docs\.google\.com/videos/d/[^/]+/edit',page.url):
                        state['document_url']=page.url.split('?')[0];save(receipt,state)
                page.set_default_timeout(20000)
                await page.bring_to_front()
                close_voice=page.get_by_role('button',name='Close dialog',exact=True)
                if await close_voice.is_visible():
                    await close_voice.click()
                await page.keyboard.press('Escape')
                if state['status'] in ('new','document_created'):
                    portrait=page.get_by_role('button',name='Create a portrait video',exact=True)
                    if state['status']=='new':
                        # Vids sometimes opens the empty editor directly, including
                        # after resuming a document saved before the chooser loaded.
                        rename=page.get_by_role('textbox',name='Rename',exact=True)
                        await portrait.or_(rename).first.wait_for(timeout=60000)
                        state.update(document_url=page.url.split('?')[0],status='document_created');save(receipt,state)
                    if await portrait.is_visible():
                        await portrait.click()
                    blank=page.get_by_role('button',name='Blank vid',exact=True)
                    if await blank.is_visible():await blank.click()
                    await page.get_by_role('textbox',name='Rename',exact=True).wait_for()
                    state['status']='document_configured';save(receipt,state)
                rename=page.get_by_role('textbox',name='Rename',exact=True)
                await rename.fill('Media Factory '+run_id)
                await rename.press('Enter')
                if not await page.get_by_role('complementary',name='AI voiceover',exact=True).is_visible():
                    await page.get_by_role('button',name='Generate a voiceover',exact=True).click()
                await page.get_by_text('Change',exact=True).wait_for()
                if not state.get('submitted_at'):
                    await page.get_by_text('Change',exact=True).click()
                    await page.get_by_role('menuitem',name=re.compile(r'\b'+re.escape(voice)+r' voice\b')).click()
                    await page.get_by_role('button',name='Select',exact=True).click()
                    placeholder=page.get_by_text('Enter',exact=True)
                    await placeholder.wait_for()
                    # This SVG text deliberately has pointer-events=none; the
                    # editor's transparent hit surface handles the same point.
                    # Foregrounding the tab is required for its rendered bounds.
                    box=await placeholder.bounding_box()
                    if not box:box=await placeholder.evaluate('(e)=>e.getBoundingClientRect().toJSON()')
                    if not box or box['width']<=0 or box['height']<=0:
                        raise RuntimeError('Vids script editor has no visible insertion surface')
                    await page.mouse.click(box['x']+box['width']/2,box['y']+box['height']/2)
                    await page.keyboard.insert_text(script)
                    # Vids renders the script as SVG words. Confirm actual entered text before generation.
                    body=' '.join((await page.locator('body').inner_text()).split())
                    if script not in body:raise RuntimeError('Vids did not retain the exact Russian script')
                    state.update(status='voice_submitted',submitted_at=time.time());save(receipt,state)
                    await page.get_by_role('button',name='Insert voiceover',exact=True).click()
                await page.get_by_role('button',name='Update voiceover',exact=True).wait_for(timeout=180000)
                body=' '.join((await page.locator('body').inner_text()).split())
                if script not in body or voice not in body:raise RuntimeError('The Vids voice or script differs from this job')
                state.update(status='voice_ready');save(receipt,state)
                await page.keyboard.press('Escape')
                await page.get_by_role('menuitem',name='File',exact=True).click()
                await page.get_by_role('menuitem',name=re.compile('^Download')).hover()
                mp4=page.get_by_role('menuitem',name=re.compile('^MP4 video'))
                await mp4.wait_for(timeout=15000)
                await expect(mp4).not_to_have_attribute('aria-disabled','true',timeout=90000)
                async with page.expect_download(timeout=180000) as info:
                    await mp4.click()
                download=await info.value;await download.save_as(str(target))
                state.update(status='completed',file=str(target),sha256=hashlib.sha256(target.read_bytes()).hexdigest(),completed_at=time.time())
                save(receipt,state)
                await page.close()
            finally:
                # The document URL/voice state is durable. Failed create pages
                # must not accumulate in the small persistent server browser.
                if page and not page.is_closed():
                    try:await asyncio.wait_for(page.close(),timeout=5)
                    except Exception:pass
                await browser.close()
    return state


if __name__=='__main__':
    print(json.dumps(asyncio.run(generate(sys.argv[1],sys.argv[2],sys.argv[3] if len(sys.argv)>3 else 'Holt')),ensure_ascii=False))
