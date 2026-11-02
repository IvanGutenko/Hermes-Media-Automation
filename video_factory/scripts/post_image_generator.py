"""Fresh Google Pics illustrations, bound to one post and a resumable document."""
import asyncio
import fcntl
import hashlib
import json
import re
import sys
import time
import subprocess
from pathlib import Path
from playwright.async_api import async_playwright,expect
from browser_session import connect

ROOT=Path('/data/output')


class ProviderUnavailable(RuntimeError):pass


async def provider_error(page):
    text=await page.locator('body').inner_text()
    if re.search(r'(?:daily|generation|usage|image).{0,40}limit|limit.{0,40}(?:reached|exceeded)|try again tomorrow|not available in your country|requires an eligible|достигнут.{0,30}лимит|лимит.{0,30}исчерпан',text,re.I):
        raise ProviderUnavailable('Google Pics explicitly reports a quota or account availability limit')


def save(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));tmp.chmod(0o644);tmp.replace(path)


async def generate(job_path,wait_browser=0):
    folder=Path(job_path).resolve().parent
    if ROOT not in folder.parents:raise ValueError('Image job outside output')
    job=json.loads(Path(job_path).read_text());receipt=folder/'pics_receipt.json';target=folder/'image.jpg'
    state=json.loads(receipt.read_text()) if receipt.exists() else {**job,'status':'new','started_at':time.time()}
    if state['prompt']!=job['prompt'] or state['key']!=job['key']:raise RuntimeError('Image job identity changed')
    if state['status']=='completed':
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=state['sha256']:raise RuntimeError('Image file differs from receipt')
        return state
    with (ROOT/'.flow-generation.lock').open('a') as lock:
        deadline=time.monotonic()+wait_browser
        while True:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise
                await asyncio.sleep(1)
        async with async_playwright() as p:
            browser=await connect(p);page=None
            try:
                ctx=browser.contexts[0]
                page=next((x for x in ctx.pages if state.get('document_url') and state['document_url'].split('?')[0] in x.url),None)
                if page is None:
                    page=await ctx.new_page()
                    await page.goto(state.get('document_url','https://docs.google.com/images/?authuser='+str(job.get('account_index',0))),
                        wait_until='domcontentloaded',timeout=45000)
                page.set_default_timeout(25000);await page.bring_to_front()
                await provider_error(page)
                if state['status']=='new':
                    # The floating Create button exists hidden on this desktop UI;
                    # the visible Blank image template is the normal creation path.
                    await page.get_by_role('listbox',name='Recently used templates',exact=True).get_by_role('option').click()
                    await page.wait_for_url(re.compile(r'/images/d/[^/]+/edit'),timeout=45000)
                    state.update(status='document_created',document_url=page.url);save(receipt,state)
                prompt=page.get_by_role('textbox',name='Bring your ideas to life with Gemini',exact=True)
                export=page.get_by_role('button',name='Export',exact=True)
                if state['status']=='document_created':
                    await prompt.fill(job['prompt'])
                    await expect(prompt).to_have_text(job['prompt'])
                    state.update(status='submitted',submitted_at=time.time());save(receipt,state)
                    await page.get_by_role('button',name='Submit',exact=True).click()
                # A lost response resumes this document. Never create or submit a
                # replacement after the provider may have accepted the request.
                deadline=time.monotonic()+240
                while not await export.is_enabled():
                    await provider_error(page)
                    if time.monotonic()>deadline:raise RuntimeError('Image generation not complete; resume this same document')
                    await asyncio.sleep(3)
                state.update(status='ready');save(receipt,state)
                await page.keyboard.press('Escape')
                await export.click()
                save(folder/'export_ui.json',{'url':page.url,'snapshot':await page.locator('body').aria_snapshot()})
                # Filled from the observed export menu; normal native download.
                original=page.get_by_role('menuitem',name=re.compile(r'Original size$'))
                async with page.expect_download(timeout=90000) as info:await original.click()
                await (await info.value).save_as(str(target))
                info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(target)]))
                stream=info['streams'][0];width,height=stream['width'],stream['height']
                if min(width,height)<600 or stream['codec_name'] not in ('mjpeg','png','webp'):
                    raise RuntimeError('Generated image has invalid dimensions or encoding')
                state.update(status='completed',file=str(target),sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                    width=width,height=height,completed_at=time.time(),provider='google_pics')
                state.pop('error',None);save(receipt,state);await page.close()
                return state
            except Exception as exc:
                if isinstance(exc,ProviderUnavailable):
                    state.update(status='provider_unavailable',failure_kind='quota_or_account_unavailable',retry_after=time.time()+3600)
                state.update(error=type(exc).__name__+': '+str(exc)[-600:],checked_at=time.time());save(receipt,state)
                if page:
                    save(folder/'last_ui.json',{'url':page.url,'snapshot':await page.locator('body').aria_snapshot()})
                raise
            finally:await browser.close()


if __name__=='__main__':print(json.dumps(asyncio.run(generate(sys.argv[1],int(sys.argv[2]) if len(sys.argv)>2 else 0)),ensure_ascii=False))
