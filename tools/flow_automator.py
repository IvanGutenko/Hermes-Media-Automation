#!/usr/bin/env python3
"""Generate exactly one new Flow clip, with a per-request provenance receipt."""
import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from browser_session import connect,hide_heavy_tabs

OUTPUT_DIR = Path('/data/output')
PROJECT_URL = os.getenv('FLOW_PROJECT_URL', 'https://flow.google.com/u/0/project/23388e4b-7da0-42d5-97a2-081e90d27c36')
MEDIA_IDS = {}
MEDIA_INFO = {}
METADATA_WAIT_SECONDS = 45
METADATA_RELOADS = 2       # one reload in about ten loses the project list although Flow answers in 3-15 s otherwise
METADATA_REFRESH_SECONDS = 20
REQUEST_LOST_SECONDS = 1800  # every clip of 2026-11 was listed within 17 min of its request


class ProviderUnavailable(RuntimeError):
    def __init__(self, message, kind):
        super().__init__(message)
        self.kind=kind


def learn_media_metadata(body):
    """Read media identities from Flow's observed project-list response.

    Batchexecute is XSSI-prefixed, length-delimited JSON. Its media records contain
    a media UUID, project UUID, creation timestamp, submitted prompt and image URLs.
    An unknown response shape fails closed instead of treating a URL as a new ID.
    """
    project_id = PROJECT_URL.rstrip('/').rsplit('/', 1)[-1]
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)
    def visit(value):
        if not isinstance(value, list):
            return
        if (len(value) == 8 and isinstance(value[0], str) and value[1] == project_id
                and re.fullmatch(r'[a-f0-9-]{36}', value[0]) and isinstance(value[5], list)):
            identity = '/image/' + value[0]
            for text in strings(value):
                if text.startswith('https://') and '/asb/' in text:
                    MEDIA_IDS[urlsplit(text).path.split('=', 1)[0]] = identity
            meta = value[5]
            if len(meta) > 1 and isinstance(meta[0], list) and meta[0] and isinstance(meta[1], str):
                MEDIA_INFO[identity] = {'created_at': meta[0][0], 'prompt': meta[1]}
        for child in value:
            visit(child)
    for line in body.splitlines():
        if not line.startswith('['):
            continue
        try:
            for row in json.loads(line):
                if isinstance(row, list) and len(row) > 2 and row[0] == 'wrb.fr' and isinstance(row[2], str):
                    visit(json.loads(row[2]))
        except (ValueError, TypeError):
            continue


async def refresh_media_metadata(page):
    # Only this reload's own project list may finish the wait: Flow sends unrelated batch responses first.
    MEDIA_IDS.clear()
    tasks = []
    ready = asyncio.Event()
    async def read(response):
        if '/data/batchexecute' not in response.url:
            return
        try:
            learn_media_metadata(await asyncio.wait_for(response.text(),timeout=45))
            if MEDIA_IDS:
                ready.set()
        except Exception:
            pass
    def listener(response):
        tasks.append(asyncio.create_task(read(response)))
    page.on('response', listener)
    try:
        for _ in range(METADATA_RELOADS):
            await page.reload(wait_until='domcontentloaded', timeout=45000)
            try:
                await asyncio.wait_for(ready.wait(), timeout=METADATA_WAIT_SECONDS)
                return
            except asyncio.TimeoutError:
                pass
        raise RuntimeError('Flow project metadata was not received; no new request submitted')
    finally:
        page.remove_listener('response', listener)
        if tasks:
            for task in tasks:
                if not task.done():task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)


class UnknownMediaIdentity(RuntimeError):
    pass


def media_id(src):
    # Signed thumbnail URLs change with time. Compare stable asset paths only.
    path = urlsplit(src).path if src else ''
    if path.startswith('/asb/'):
        identity = MEDIA_IDS.get(path.split('=', 1)[0])
        if not identity:
            raise UnknownMediaIdentity('Flow thumbnail is missing a verified media identity')
        return identity
    return path.replace('/video/', '/image/', 1)


async def video_tiles(page):
    records = []
    for tile in await page.locator('flow-grid-tile-container').all():
        if not await tile.locator('flow-video-tile').count():
            continue
        image = tile.locator('img.thumbnail')
        src = await image.get_attribute('src') if await image.count() else ''
        video = tile.locator('video[src]')
        if not src and await video.count():
            src = await video.get_attribute('src')
        if src:
            records.append((media_id(src), tile))
    return records


async def wait_flow_entry(page,timeout=30):
    # Flow's Trusted Types policy rejects string-evaluated DOM predicates.
    # Observe its actual route and accessible control instead.
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if '/unsupported-country' in page.url or '/project/' in page.url:return
        if await page.get_by_role('button',name=re.compile(r'New project|Новый проект')).is_visible():return
        await asyncio.sleep(.5)
    raise TimeoutError('Flow did not expose a project, region refusal or new-project control')


async def require_generation_credits(page):
    # This warning contains an <img> inside the prompt box. It is a provider
    # refusal, not an attached ingredient. Only check before a NEW submission:
    # an exhausted balance must never prevent collection of an existing clip.
    if await page.get_by_role('button', name='Insufficient credits warning', exact=True).is_visible():
        raise ProviderUnavailable('Flow reports insufficient generation credits', 'quota_exhausted')


async def configure_video(page, native_audio=False, model_name='Veo 3.1 - Lite'):
    # Flow can restore this banner on reload, covering the prompt/settings bar.
    # Dismiss it through its own control before operating the generator.
    cookie_banner = page.locator('.glue-cookie-notification-bar')
    if await cookie_banner.is_visible():
        await cookie_banner.get_by_role('button', name='Hide', exact=True).click()
        await cookie_banner.wait_for(state='hidden')
    done = page.get_by_role('button', name='Done editing scene', exact=True)
    if await done.is_visible():
        await done.click()
        await page.get_by_role('textbox', name='What do you want to create?', exact=True).wait_for()
    await require_generation_credits(page)
    await page.keyboard.press('Escape')
    settings_button = page.get_by_role('button', name='Settings', exact=True)
    silent = page.get_by_role('switch', name='Return silent videos toggle', exact=True)
    if not await silent.is_visible():
        await settings_button.click()
    expected_silent='false' if native_audio else 'true'
    if await silent.get_attribute('aria-checked') != expected_silent:
        await silent.click()
    await expect(silent).to_have_attribute('aria-checked', expected_silent)
    await page.keyboard.press('Escape')
    await silent.wait_for(state='hidden')
    agent = page.locator('flow-agent-mode-toggle-chip button')
    if await agent.count() and await agent.get_attribute('aria-pressed') == 'true':
        await agent.click()
    if await page.locator('flow-base-prompt-box img').count():
        raise RuntimeError('Remove attached ingredients before an avatar-free text-to-video request')
    model = page.get_by_role('button', name='Select model family', exact=True)
    if not await model.is_visible():
        await page.get_by_role('button', name='Settings trigger', exact=True).click()
    await page.get_by_role('radio', name='Video', exact=True).click()
    await page.get_by_role('radio', name='9:16', exact=True).click()
    await page.get_by_role('radio', name='x1', exact=True).click()
    await model.click()
    limits={'Veo 3.1 - Lite':10,'Veo 3.1 - Fast':20}
    if model_name not in limits:raise ValueError('Unsupported included-credit model')
    await page.get_by_role('menuitem').filter(has_text=model_name).click()
    await expect(model).to_contain_text(model_name)
    credit_link=page.get_by_role('link',name=re.compile(r'^\d+ credits$'))
    await credit_link.wait_for()
    settings = await page.locator('.cdk-overlay-container').inner_text()
    cost = re.fullmatch(r'(\d+)\s+credits', (await credit_link.inner_text()).strip())
    if not cost or int(cost.group(1)) > limits[model_name]:
        raise RuntimeError('Unexpected generation cost for the selected included-credit model')
    for text in ('Video', '9:16', 'x1'):
        radio = page.get_by_role('radio', name=text, exact=True)
        if await radio.get_attribute('aria-checked') != 'true':
            raise RuntimeError('Flow setting did not persist: ' + text)
    if model_name not in settings:
        raise RuntimeError('Flow video model is not selected')
    # This settings popover does not reliably close on Escape.
    await page.get_by_role('button', name='Settings trigger', exact=True).click()
    return {'model': model_name, 'aspect_ratio': '9:16', 'count': 1,
            'credits': int(cost.group(1)), 'mode': 'direct_text_to_video', 'return_silent_videos': not native_audio}


async def download_video(page, target, quality):
    # A newly finished card can precede availability of its downloadable blob.
    # Retry only the download of this selected asset, never generation itself.
    for attempt in range(3):
        await page.get_by_role('button', name='Download media', exact=True).click()
        menu = page.get_by_role('menuitem').filter(has_text=quality)
        if not await menu.count():
            raise RuntimeError('Requested download quality is unavailable')
        async with page.expect_download(timeout=60000) as info:
            await menu.first.click()
        download = await info.value
        await download.save_as(target)
        if target.stat().st_size >= 10000:
            return
        print(f'Empty download for the NEW asset; retry {attempt + 1}/3', flush=True)
        await page.wait_for_timeout(3000)
    raise RuntimeError('The new asset still downloads as an empty file')


def own_request_tiles(fresh, submitted_prompt, submitted_at):
    """Keep the new tiles whose recorded prompt and creation time are this request's.

    Brands share one Flow project. While one request is retained for recovery, another
    brand can add tiles after its baseline, so "one new tile" no longer identifies it.
    The submitted prompt carries the scene's own narration, so an exact match created
    after submission is the same proof the download step already requires.
    """
    wanted = ' '.join(submitted_prompt.split())
    return [(key, tile) for key, tile in fresh
            if key in MEDIA_INFO and ' '.join(MEDIA_INFO[key]['prompt'].split()) == wanted
            and MEDIA_INFO[key]['created_at'] >= submitted_at - 2]


def request_is_lost(submitted_prompt, submitted_at, now=None):
    """True when a just loaded project list has no record of a request old enough to be listed.

    Flow records a request the moment it is accepted. Without this a lost request is retried until RETAINED_MAX_SECONDS.
    """
    if not (MEDIA_INFO and submitted_prompt and submitted_at) or (now or time.time()) - submitted_at < REQUEST_LOST_SECONDS:
        return False
    wanted = ' '.join(submitted_prompt.split())
    return not any(' '.join(info['prompt'].split()) == wanted and info['created_at'] >= submitted_at - 2 for info in MEDIA_INFO.values())


async def wait_for_new_tile(page, baseline, timeout_seconds, baseline_errors=None, submitted_prompt=None, submitted_at=None):
    initial_errors = Counter(baseline_errors if baseline_errors is not None else
                             await page.locator('.error-message-text').all_inner_texts())
    deadline = time.monotonic() + timeout_seconds
    next_refresh = 0
    while time.monotonic() < deadline:
        await page.wait_for_timeout(4000)
        try:
            tiles = await video_tiles(page)
        except UnknownMediaIdentity:
            # A new clip's thumbnail is in the gallery, its identity only in the project list of the next page load.
            # The reload submits nothing and leaves the request running in Flow.
            if time.monotonic() >= next_refresh:
                await refresh_media_metadata(page)
                next_refresh = time.monotonic() + METADATA_REFRESH_SECONDS
            continue
        fresh = [(key, tile) for key, tile in tiles if key not in baseline]
        if fresh and submitted_prompt and submitted_at:
            own = own_request_tiles(fresh, submitted_prompt, submitted_at)
            if len(own) == 1:
                fresh = own
            elif len(fresh) == 1 and fresh[0][0] in MEDIA_INFO:
                fresh = []  # the only new tile is recorded with another request's prompt: not this result
        if len(fresh) > 1:
            raise RuntimeError('Multiple new media assets; cannot identify a single request result')
        if fresh and not await fresh[0][1].locator('.loading-percentage').count():
            return fresh[0]
        errors = await page.locator('.error-message-text').all_inner_texts()
        new_errors = Counter(errors) - initial_errors
        if new_errors:
            message=next(iter(new_errors)).strip()
            if re.search(r'not enough credits|insufficient credits|out of (?:AI )?credits|недостаточно кредитов|кредиты закончились',message,re.I):
                raise ProviderUnavailable(message,'quota_exhausted')
            if re.fullmatch(r'Generation timed out\.\s*Please try again\.',message,re.I):
                # This is the new failed tile's explicit terminal response,
                # distinct from our own timeout while a request is still pending.
                raise ProviderUnavailable(message,'generation_failed')
            raise RuntimeError('Flow generation failed: ' + message)
    raise TimeoutError('No new video asset was produced by the submitted request')


async def leave_editor(page):
    """Tidy up after a verified clip. The page may hang after this click, and the next run closes the editor anyway, so
    it must not turn a finished, verified result into an error (seen twice: a retry and 15 minutes lost each time)."""
    try:
        done = page.get_by_role('button', name='Done editing scene', exact=True)
        if await done.is_visible():
            await done.click(no_wait_after=True)
    except Exception as exc:
        print(f'Flow editor stayed open: {type(exc).__name__}', flush=True)


async def generate_flow_video(prompt, title='Shorts Video', download_quality='720p', timeout_seconds=420, run_id=None, resume_download=False, voiceover_ru=None, model_name='Veo 3.1 - Lite', project_url=None):
    global PROJECT_URL
    if project_url:PROJECT_URL=project_url
    run_id = run_id or uuid.uuid4().hex
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', run_id):
        raise ValueError('Invalid run id')
    if not prompt.strip() or re.search(r'[А-Яа-яЁё]', prompt):
        raise ValueError('An English visual prompt is required')
    if voiceover_ru and not re.fullmatch(r'[А-Яа-яЁё ]+', voiceover_ru):
        raise ValueError('Native narration must contain Russian letters and spaces only')
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    receipt = OUTPUT_DIR / f'flow_{run_id}.json'
    target = OUTPUT_DIR / f'flow_{run_id}.mp4'
    if (receipt.exists() or target.exists()) and not resume_download:
        raise RuntimeError('Run id already exists; use a new id')
    if resume_download:
        result = json.loads(receipt.read_text())
        if (result.get('voiceover_ru') != voiceover_ru or result.get('run_id') != run_id or result.get('prompt_sha256') != hashlib.sha256(prompt.encode()).hexdigest()
                or not (result.get('submitted_at') or result.get('submission_attempted_at')) or 'baseline_media_ids' not in result
                or (result.get('media_id') and result['media_id'] in result['baseline_media_ids'])):
            raise RuntimeError('Cannot resume: no correlated new asset in this request receipt')
        result['download_resumed_at'] = time.time()
        PROJECT_URL=result.get('project_url',PROJECT_URL)
    else:
        result = {'run_id': run_id, 'status': 'started', 'title': title, 'prompt': prompt,
                  'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                  'started_at': time.time(), 'video_file': None, 'voiceover_ru': voiceover_ru, 'audio_mode': 'native' if voiceover_ru else 'silent'}

    def save():
        temp = receipt.with_suffix('.tmp')
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        temp.replace(receipt)
    save()
    with (OUTPUT_DIR / '.flow-generation.lock').open('a') as lock:
        # Vids, Studio, Dzen and Flow share one signed-in, memory-limited browser.
        # Waiting for its lease is normal; it is not a failed video generation.
        fcntl.flock(lock, fcntl.LOCK_EX)
        async with async_playwright() as p:
            browser = None
            page = None
            try:
                browser = await connect(p)
                context = browser.contexts[0]
                page = next((x for x in context.pages if urlsplit(x.url).netloc=='flow.google.com'
                    and urlsplit(x.url).path==urlsplit(PROJECT_URL).path), None)
                if page is None:
                    page = await context.new_page()
                    await page.goto(PROJECT_URL, wait_until='domcontentloaded', timeout=45000)
                await page.bring_to_front()
                if '/project/' not in page.url:
                    await wait_flow_entry(page)
                if '/unsupported-country' in page.url:
                    raise ProviderUnavailable('Flow account region is unsupported','unsupported_country')
                if '/project/' not in page.url:
                    await page.get_by_role('button',name=re.compile(r'New project|Новый проект')).click()
                    await page.wait_for_url(re.compile(r'/project/[a-f0-9-]+'),timeout=30000)
                PROJECT_URL=page.url.split('?')[0]
                result['project_url']=PROJECT_URL
                result['stage']='opening_editor';save()
                decline=page.get_by_role('button',name='No thanks',exact=True)
                if await decline.is_visible():await decline.click()
                page.set_default_timeout(15000)
                done = page.get_by_role('button', name='Done editing scene', exact=True)
                if await done.is_visible():
                    await done.click()
                    await page.get_by_role('textbox', name='What do you want to create?', exact=True).wait_for()
                if not resume_download:
                    await page.get_by_role('textbox', name='What do you want to create?', exact=True).wait_for(timeout=45000)
                    await require_generation_credits(page)
                result['stage']='loading_project_metadata';save()
                await refresh_media_metadata(page)
                result['metadata_ready_at']=time.time()
                result['stage']='waiting_for_editor_after_reload';save()
                # Metadata arrives before the editor finishes mounting on a large
                # gallery. The earlier readiness wait was invalidated by reload.
                editor_control=page.get_by_role('textbox',name='What do you want to create?',exact=True).or_(
                    page.get_by_role('button',name='Done editing scene',exact=True))
                await editor_control.first.wait_for(state='visible',timeout=60000)
                result['editor_ready_after_reload_at']=time.time()
                result['stage']='configuring_video';save()
                if resume_download:
                    if request_is_lost(result.get('submitted_prompt'), result.get('submitted_at') or result.get('submission_attempted_at')):
                        raise ProviderUnavailable('Flow has no record of this request; it was lost', 'generation_failed')
                    done = page.get_by_role('button', name='Done editing scene', exact=True)
                    if await done.is_visible():
                        await done.click()
                    await page.get_by_role('textbox', name='What do you want to create?', exact=True).wait_for()
                    # Done resolves before the gallery thumbnails have loaded.
                    if result.get('media_id'):
                        asset = result['media_id'].rsplit('/', 1)[-1]
                        matches = [tile for key, tile in await video_tiles(page) if key == result['media_id']]
                        if len(matches) != 1:
                            raise RuntimeError('The specific new asset is unavailable for download recovery')
                        new_tile = matches[0]
                    else:
                        # Recover observation of an already submitted request. Do not submit again.
                        result['media_id'], new_tile = await wait_for_new_tile(page, set(result['baseline_media_ids']), timeout_seconds,
                            submitted_prompt=result.get('submitted_prompt'),
                            submitted_at=result.get('submitted_at') or result.get('submission_attempted_at'))
                else:
                    result['settings'] = await configure_video(page, native_audio=bool(voiceover_ru),model_name=model_name)
                    result['stage']='observing_baseline';save()
                    if await page.locator('.loading-percentage').count():
                        raise RuntimeError('Flow already has a pending generation; cannot correlate a new result')
                    baseline = {key for key, _ in await video_tiles(page)}
                    result['baseline_media_ids'] = sorted(baseline)
                    result['stage']='preparing_submission';save()
                    baseline_errors = await page.locator('.error-message-text').all_inner_texts()
                    box = page.get_by_role('textbox', name='What do you want to create?', exact=True)
                    submitted_prompt = prompt + (' Vertical 9:16. Follow the specified shot changes and editing cuts. Clean graphic surfaces. '
                        'Readable lettering and subtitles are absent. No character reference images are used.')
                    if voiceover_ru:
                        submitted_prompt += (' Audio: one off-screen adult male narrator speaks native Russian, '
                            'ru-RU, with natural Moscow pronunciation and confident conversational delivery, '
                            'without an English accent. Finish the entire sentence within this eight-second clip. '
                            'Only the exact Russian transcript below is spoken, once, with no other words. '
                            'The narrator remains off screen. No characters talk. No music or singing. '
                            'Do not read visual or editing instructions aloud. Russian narration transcript: "' + voiceover_ru + '".')
                    else:
                        submitted_prompt += ' Audio: ambient room tone only; speech, dialogue and singing are absent.'
                    result['submitted_prompt'] = submitted_prompt
                    await box.fill(submitted_prompt)
                    result.update(status='submission_attempted',submission_attempted_at=time.time());save()
                    await page.get_by_role('button', name='Start generation', exact=True).click()
                    result.update(status='submitted', submitted_at=time.time())
                    save()
                    result['media_id'], new_tile = await wait_for_new_tile(page, baseline, timeout_seconds, baseline_errors,
                        submitted_prompt=submitted_prompt, submitted_at=result['submitted_at'])
                await new_tile.click()
                await page.get_by_role('button', name='Download media', exact=True).wait_for()
                editor_text = ' '.join((await page.locator('body').inner_text()).split())
                if ' '.join(result['submitted_prompt'].split()) not in editor_text:
                    raise RuntimeError('The selected asset has a different generation prompt')
                result['prompt_verified_in_editor'] = True
                info = MEDIA_INFO.get(result['media_id'])
                if info:
                    if info['created_at'] < result.get('submitted_at',result.get('submission_attempted_at')) - 2:
                        raise RuntimeError('The selected clip was created before this request')
                    if ' '.join(info['prompt'].split()) != ' '.join(result['submitted_prompt'].split()):
                        raise RuntimeError('Flow media metadata contains a different generation prompt')
                    result['media_metadata'] = info
                await download_video(page, target, download_quality)
                probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                    '-show_format', '-of', 'json', str(target)], text=True))
                stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
                if stream['width'] >= stream['height']:
                    raise RuntimeError('Generated clip is not vertical')
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                for old in OUTPUT_DIR.glob('*.mp4'):
                    if (old != target and old.stat().st_mtime < result['started_at']
                            and old.stat().st_size == target.stat().st_size):
                        if hashlib.sha256(old.read_bytes()).hexdigest() == digest:
                            raise RuntimeError('Downloaded video duplicates a pre-existing file')
                result.update(status='completed', video_file=str(target), sha256=digest,
                              completed_at=time.time(), duration=float(probe['format']['duration']),
                              width=stream['width'], height=stream['height'])
                result.pop('error', None)
                print(f'NEW generated asset: {result["media_id"]} -> {target}', flush=True)
                await leave_editor(page)
            except Exception as exc:
                result.update(status='error', error=type(exc).__name__+': '+str(exc))
                if isinstance(exc,ProviderUnavailable):
                    result.update(failure_kind=exc.kind,terminal_failure_observed=True)
                if page:
                    try:
                        await page.screenshot(path=str(OUTPUT_DIR / f'flow_{run_id}_error.png'))
                    except Exception:
                        pass
            finally:
                save()
                if page and (result.get('status')=='completed' or result.get('terminal_failure_observed')
                        or not (result.get('submitted_at') or result.get('submission_attempted_at'))):
                    try:await asyncio.wait_for(page.close(),timeout=5)
                    except Exception:pass
                # A retained project page stays open for the next tick. Kept on top it renders without pause in software:
                # measured 2026-11-02 at 105 % + 44 % of the host's 200 % CPU, faulting its swapped pages back in all the time.
                try:await asyncio.wait_for(hide_heavy_tabs(context),timeout=8)
                except Exception:pass
                if browser:
                    try:await asyncio.wait_for(browser.close(),timeout=5)
                    except Exception:pass
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prompt')
    parser.add_argument('title', nargs='?', default='Shorts Video')
    parser.add_argument('--run-id')
    parser.add_argument('--resume-download', action='store_true')
    parser.add_argument('--project-url')
    parser.add_argument('--speech-ru', help='Exact separate Russian narration for native Veo audio')
    parser.add_argument('--model',choices=['Veo 3.1 - Lite','Veo 3.1 - Fast'],default='Veo 3.1 - Lite')
    parser.add_argument('--timeout', type=int, default=420)
    args = parser.parse_args()
    result = asyncio.run(generate_flow_video(args.prompt, args.title, run_id=args.run_id,
        timeout_seconds=args.timeout, resume_download=args.resume_download, voiceover_ru=args.speech_ru,model_name=args.model,project_url=args.project_url))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys_exit = 0 if result['status'] == 'completed' else 1
    raise SystemExit(sys_exit)
