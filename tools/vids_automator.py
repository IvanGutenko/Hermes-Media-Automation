#!/usr/bin/env python3
"""One new Vids video clip per durable request; never treat an editor URL as a video."""
import argparse
import asyncio
import fcntl
import hashlib
import json
import re
import subprocess
import time
import uuid
from pathlib import Path
from playwright.async_api import async_playwright, expect
from browser_session import connect, hide_heavy_tabs, set_frozen

OUTPUT_DIR = Path('/data/output')


def save(path, value):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2));temp.replace(path)


def verified_file(state, target):
    return (state.get('status')=='completed' and target.is_file()
            and hashlib.sha256(target.read_bytes()).hexdigest()==state.get('sha256'))


class FeatureUnavailable(RuntimeError):
    pass


async def open_generation_panel(page,state):
    """Handle a chooser or a blank editor; do not wait forever for a missing feature.

    Account 1's loaded start screen has avatar/voice/image tools but no video
    generation option. Confirm this again after reloading before declaring the
    provider unavailable. This helper is only used before any submission.
    """
    for observation in range(2):
        create=page.get_by_role('button',name=re.compile(r'^(Create AI videos|Создание ИИ-видео|Создать ИИ-видео)'))
        portrait=page.get_by_role('button',name=re.compile(r'^(Create a portrait video|Создать вертикальное видео|Вертикальное)$'))
        rename=page.get_by_role('textbox',name=re.compile(r'^(Rename|Переименовать)$'))
        await create.or_(portrait).or_(rename).first.wait_for(timeout=60000)
        if await create.is_visible():
            if await portrait.is_visible():await portrait.click()
            await create.click();return
        blank=page.get_by_role('button',name=re.compile(r'^(Blank vid|Пустой файл Vids)$'))
        if await portrait.is_visible() and await blank.is_visible():
            # A loaded chooser itself shows which features this account has.
            # Do not change orientation or open Blank when AI video is absent:
            # that can disable/rebuild the cards during the second observation.
            await expect(blank).to_be_enabled(timeout=60000)
            surface='start_chooser'
        else:
            await rename.wait_for(timeout=30000)
            box=page.get_by_role('textbox',name=re.compile(r'^(Describe|Опишите видео)'))
            if await box.is_visible():return
            reopen=page.get_by_role('button',name=re.compile(r'^(Generate an AI video clip|Сгенерировать видеоклип от ИИ)$'))
            if await reopen.is_visible():
                await reopen.click();return
            # Login/network/loading failures remain uncertain.
            voice=page.get_by_role('button',name=re.compile(r'^(Generate a voiceover|Сгенерировать закадровую речь)$'))
            await voice.wait_for(timeout=20000)
            surface='editor'
        state.setdefault('availability_observations',[]).append({
            'observed_at':time.time(),'url':page.url,
            'body':await page.locator('body').inner_text(timeout=15000),
            'video_option_visible':False,'surface':surface,'surface_ready':True})
        if observation==0:await page.reload(wait_until='domcontentloaded',timeout=45000)
    raise FeatureUnavailable('Vids AI video option is absent in two loaded start/editor observations; no generation submitted')


async def generate_vids_video(prompt, title='AI Video', *, run_id=None, account=1, timeout_seconds=420):
    run_id=run_id or uuid.uuid4().hex
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',run_id):raise ValueError('Invalid request id')
    if len(prompt)<60 or re.search(r'[А-Яа-яЁё]',prompt):raise ValueError('English visual instructions are required')
    OUTPUT_DIR.mkdir(exist_ok=True)
    receipt=OUTPUT_DIR/f'vids_{run_id}.json';target=receipt.with_suffix('.mp4')
    digest=hashlib.sha256(prompt.encode()).hexdigest()
    state=json.loads(receipt.read_text()) if receipt.exists() else {
        'run_id':run_id,'provider':'vids','account':account,'status':'new',
        'prompt':prompt,'prompt_sha256':digest,'title':title,'started_at':time.time()}
    if state['run_id']!=run_id or state['prompt_sha256']!=digest or state.get('account')!=account:
        raise RuntimeError('Vids receipt belongs to another request')
    if state.get('status')=='completed':
        if not verified_file(state,target):raise RuntimeError('Completed Vids asset differs from its receipt')
        return state
    with (OUTPUT_DIR/'.flow-generation.lock').open('a') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX)
        async with async_playwright() as p:
            browser=await connect(p);page=None
            try:
                ctx=browser.contexts[0]
                page=next((x for x in ctx.pages if state.get('document_url') and state['document_url'] in x.url),None)
                if page is None:
                    page=await ctx.new_page()
                    await page.goto(state.get('document_url',f'https://docs.google.com/videos/create?authuser={account}'),wait_until='commit',timeout=60000)
                    await page.wait_for_url(re.compile(r'https://docs\.google\.com/videos/d/[^/]+/edit'),wait_until='commit',timeout=60000)
                    state['document_url']=page.url.split('?')[0]
                    if state['status']=='new':state.update(status='document_created',phase='document_created')
                    save(receipt,state)
                else:
                    await set_frozen(page, False)
                page.set_default_timeout(20000)
                if state.get('submission_attempted_at') or state.get('submitted_at'):
                    # A reloaded document opens its canvas, not the generation sidebar.
                    # Waiting seven minutes on that empty canvas blocks every publisher.
                    await page.get_by_role('textbox',name=re.compile(r'^(Rename|Переименовать)$')).wait_for(timeout=60000)
                    generated=page.get_by_role('button',name=re.compile(r'^(Generated video (?:for|from) prompt|Сгенерированное видео по запросу)'))
                    box=page.get_by_role('textbox',name=re.compile(r'^(Describe|Опишите видео)'))
                    if not await generated.count() and not await box.count():
                        reopen=page.get_by_role('button',name=re.compile(r'^(Generate an AI video clip|Сгенерировать видеоклип от ИИ)$'))
                        try:await reopen.click(timeout=20000)
                        except Exception as exc:
                            raise RuntimeError('Saved Vids document has no accessible generation panel; original request retained') from exc
                if not state.get('submission_attempted_at') and not state.get('submitted_at'):
                    await open_generation_panel(page,state)
                    state['phase']='configured';save(receipt,state)
                    rename=page.get_by_role('textbox',name=re.compile(r'^(Rename|Переименовать)$'))
                    await rename.fill('Media Factory '+run_id)
                    settings=page.get_by_role('button',name=re.compile(r'^(Generation settings|Настройки генерации):'))
                    await settings.wait_for();await settings.click()
                    await page.get_by_role('radio',name=re.compile(r'^(Portrait|Vertical|Вертикальное)$')).click()
                    await page.get_by_role('slider',name=re.compile(r'^(Video length|Длина видео)$')).fill('8')
                    await settings.click()
                    config=await settings.get_attribute('aria-label')
                    if not re.search(r'Portrait|Vertical|Вертикальное',config) or not re.search(r'\b8\b',config):
                        raise RuntimeError('Vids did not retain portrait/eight-second generation settings')
                    box=page.get_by_role('textbox',name=re.compile(r'^(Describe|Опишите видео)'))
                    await box.fill(prompt)
                    if (await box.inner_text()).strip()!=prompt:raise RuntimeError('Vids prompt differs from this request')
                    baseline=await page.locator('video[src]').evaluate_all('(es)=>es.map(e=>e.src)')
                    if baseline:raise RuntimeError('A fresh Vids document unexpectedly already contains video')
                    state.update(settings=config,baseline_video_sources=baseline,status='submission_attempted',submission_attempted_at=time.time())
                    save(receipt,state)
                    await page.get_by_role('button',name=re.compile(r'^(Generate|Сгенерировать)$')).click()
                    state.update(status='submitted',submitted_at=time.time());save(receipt,state)
                # A timeout resumes observation of this same document. It never generates again.
                deadline=time.monotonic()+timeout_seconds
                generated=page.get_by_role('button',name=re.compile(r'^(Generated video (?:for|from) prompt|Сгенерированное видео по запросу)'))
                while time.monotonic()<deadline:
                    if await generated.count() and await generated.first.is_visible():break
                    alerts=' '.join(await page.get_by_role('alert').all_inner_texts())
                    if re.search(r'(?:reached|exceeded).{0,50}(?:limit|quota)|(?:limit|quota).{0,50}(?:reached|exceeded)|лимит.{0,50}исчерпан',alerts,re.I):
                        state['failure_kind']='quota_exhausted';state['terminal_failure_observed']=True
                        raise RuntimeError('Vids rejected generation: '+alerts[:400])
                    await page.wait_for_timeout(3000)
                else:raise TimeoutError('The submitted Vids request has no correlated completed result yet')
                label=await generated.first.get_attribute('aria-label')
                if prompt not in label:raise RuntimeError('Generated Vids preview has a different prompt')
                videos=page.locator('video[src]')
                if await videos.count()!=1:raise RuntimeError('Vids result is ambiguous')
                src=await videos.get_attribute('src')
                if src in state['baseline_video_sources']:raise RuntimeError('Vids preview is a pre-existing asset')
                state.update(prompt_verified_in_editor=True,generation_label=label,status='downloading');save(receipt,state)
                response=await ctx.request.get(src,timeout=60000)
                if not response.ok or 'video' not in response.headers.get('content-type',''):
                    raise RuntimeError('Vids media download HTTP '+str(response.status))
                target.write_bytes(await response.body())
                info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(target)]))
                stream=next(x for x in info['streams'] if x['codec_type']=='video')
                if stream['width']>=stream['height'] or not 7.5<=float(info['format']['duration'])<=8.5:
                    raise RuntimeError('Vids downloaded file has the wrong dimensions or duration')
                sha=hashlib.sha256(target.read_bytes()).hexdigest()
                state.update(status='completed',video_file=str(target),sha256=sha,
                    media_id='vids:'+state['document_url'].split('/d/')[1].split('/')[0]+':'+sha[:16],
                    duration=float(info['format']['duration']),width=stream['width'],height=stream['height'],completed_at=time.time())
                state.pop('error',None);save(receipt,state)
                try:
                    await page.get_by_role('button',name=re.compile(r'^(Insert|Вставить)$')).click()
                    await page.wait_for_timeout(1500)
                    state['inserted_into_document']=True;save(receipt,state)
                except Exception:
                    # The validated MP4 is already durable even if saving its Vids canvas fails.
                    state['document_insert_unverified']=True;save(receipt,state)
            except Exception as exc:
                if isinstance(exc,FeatureUnavailable):
                    state.update(failure_kind='feature_unavailable',terminal_failure_observed=True)
                state.update(status='error',error=str(exc));save(receipt,state)
            finally:
                if page:
                    try:
                        if (state.get('status')=='completed' or state.get('terminal_failure_observed')
                                or not (state.get('submission_attempted_at') or state.get('submitted_at'))):
                            await asyncio.wait_for(page.close(),5)
                        else:
                            # Keep the unsaved result recoverable, but stop a retained editor
                            # from faulting its swapped pages back in throughout the next jobs.
                            await set_frozen(page, True)
                        await asyncio.wait_for(hide_heavy_tabs(ctx),5)
                    except Exception:pass
                try:await asyncio.wait_for(browser.close(),5)
                except Exception:pass
    return state


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prompt');parser.add_argument('title',nargs='?',default='AI Video')
    parser.add_argument('--run-id');parser.add_argument('--account',type=int,default=1)
    args=parser.parse_args()
    result=asyncio.run(generate_vids_video(args.prompt,args.title,run_id=args.run_id,account=args.account))
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['status']=='completed' else 1)
