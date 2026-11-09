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
class ResultLostBeforeConfirmation(RuntimeError):pass


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
    if state['status']=='result_lost_before_confirmation':
        raise ResultLostBeforeConfirmation('Completed provider preview was lost before confirmation; original attempt retained')
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
                # Pics first offers four previews. Export may already be enabled
                # while they still need Confirm, so it is not a final-artifact signal.
                previews=page.get_by_role('radiogroup',name='Image previews',exact=True)
                confirm=page.get_by_role('button',name='Confirm',exact=True)
                old_preview=folder/'export_ui.json'
                old_empty=folder/'last_ui.json'
                prior=json.loads(old_empty.read_text()) if old_empty.exists() else {}
                old=json.loads(old_preview.read_text()) if old_preview.exists() else {}
                same_document=lambda url:(url or '').split('?')[0]==state.get('document_url','').split('?')[0]
                if (state['status']=='ready' and not state.get('confirmed_at') and old_preview.exists()
                        and 'radiogroup "Image previews"' in old.get('snapshot','')
                        and 'Image generation not complete; resume this same document' in state.get('error','')
                        and same_document(old.get('url')) and same_document(prior.get('url'))
                        and 'heading "Drop or select an image to start editing"' in prior.get('snapshot','')
                        and 'button "Export" [disabled]' in prior.get('snapshot','')
                        and 'radiogroup "Image previews"' not in prior.get('snapshot','')):
                    # The old exporter reached an actual generated preview, then
                    # lost it without committing an image to this Drive document.
                    # Require the loaded, empty editor too; a pending request or a
                    # missing browser response never authorizes a replacement.
                    empty=page.get_by_role('heading',name='Drop or select an image to start editing',exact=True)
                    if await empty.is_visible() and not await previews.is_visible() and not await export.is_enabled():
                        await page.wait_for_timeout(3000)
                        if await empty.is_visible() and not await previews.is_visible() and not await export.is_enabled():
                            state.update(status='result_lost_before_confirmation',result_loss_observed_at=time.time(),
                                result_loss_evidence={'reason':'Generated preview was never confirmed; prior full wait and current loaded document are empty',
                                    'preview_snapshot_sha256':hashlib.sha256(old_preview.read_bytes()).hexdigest(),
                                    'empty_snapshot_sha256':hashlib.sha256(old_empty.read_bytes()).hexdigest(),
                                    'prior_timeout_observed_at':state.get('checked_at')})
                            save(receipt,state)
                            raise ResultLostBeforeConfirmation('Generated previews were lost before Confirm; original document retained')
                empty=page.get_by_role('heading',name='Drop or select an image to start editing',exact=True)
                if (state.get('status') in ('submitted','ready') and not state.get('confirmed_at')
                        and time.time()-state.get('submitted_at',time.time())>900
                        and await empty.is_visible() and not await previews.is_visible()
                        and not await page.get_by_role('button',name='Retry',exact=True).is_visible()
                        and not await page.get_by_role('button',name='Submit',exact=True).is_enabled()):
                    await page.wait_for_timeout(3000)
                    if (await empty.is_visible() and not await previews.is_visible()
                            and not await page.get_by_role('button',name='Retry',exact=True).is_visible()
                            and not await page.get_by_role('button',name='Submit',exact=True).is_enabled()):
                        snapshot=await page.locator('body').aria_snapshot()
                        save(folder/'empty_recovery_ui.json',{'url':page.url,'snapshot':snapshot})
                        state.update(status='empty_document_after_submission',empty_observed_at=time.time(),
                            empty_evidence_sha256=hashlib.sha256(snapshot.encode()).hexdigest())
                        save(receipt,state)
                        raise ResultLostBeforeConfirmation('Stale image request has an empty loaded document; original retained')
                deadline=time.monotonic()+240
                while True:
                    await provider_error(page)
                    retry=page.get_by_role('button',name='Retry',exact=True)
                    if await retry.is_visible():
                        # An explicit failed generation may be retried on the SAME
                        # document. Persist the bounded attempt before clicking.
                        if state.get('provider_retries',0)>=2:
                            raise ProviderUnavailable('Google Pics failed after two explicit Retry attempts')
                        state.update(provider_retries=state.get('provider_retries',0)+1,
                            retry_attempted_at=time.time());save(receipt,state)
                        await retry.click()
                        await retry.wait_for(state='hidden',timeout=30000)

                    if await previews.is_visible():
                        selected=previews.get_by_role('radio',checked=True)
                        if await selected.count()!=1:raise RuntimeError('Pics preview selection is ambiguous')
                        await expect(confirm).to_be_enabled(timeout=30000)
                        state.update(confirm_attempted_at=time.time());save(receipt,state)
                        await confirm.click()
                        await previews.wait_for(state='hidden',timeout=30000)
                        state.update(confirmed_at=time.time());save(receipt,state)
                    if await export.is_enabled() and state.get('confirmed_at'):break
                    if time.monotonic()>deadline:raise RuntimeError('Image generation not complete; resume this same document')
                    await asyncio.sleep(3)
                # Confirm commits a preview asynchronously. Exporting while
                # Drive still says Saving produced a zero-byte native download.
                saving=page.get_by_role('button',name=re.compile(r'^Document status: Saving'))
                if await saving.count():await saving.wait_for(state='hidden',timeout=60000)
                await expect(page.get_by_role('button',name='Copy to clipboard',exact=True)).to_be_enabled(timeout=30000)
                state.update(status='ready');save(receipt,state)
                await page.keyboard.press('Escape')
                await export.click()
                save(folder/'export_ui.json',{'url':page.url,'snapshot':await page.locator('body').aria_snapshot()})
                # Filled from the observed export menu; normal native download.
                original=page.get_by_role('menuitem',name=re.compile(r'Original size$'))
                async with page.expect_download(timeout=90000) as info:await original.click()
                download=await info.value
                temporary=target.with_suffix('.download.jpg')
                await download.save_as(str(temporary))
                failure=await download.failure()
                state.update(download_filename=download.suggested_filename,download_checked_at=time.time())
                save(receipt,state)
                if failure or not temporary.exists() or temporary.stat().st_size<1024:
                    raise RuntimeError('Pics export has no usable bytes; confirmed document retained for download retry')
                temporary.replace(target)
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
                    if state.get('status') in ('empty_document_after_submission','result_lost_before_confirmation','provider_unavailable'):
                        await page.close()
                raise
            finally:await browser.close()


if __name__=='__main__':print(json.dumps(asyncio.run(generate(sys.argv[1],int(sys.argv[2]) if len(sys.argv)>2 else 0)),ensure_ascii=False))
