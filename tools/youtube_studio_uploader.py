#!/usr/bin/env python3
"""Server browser uploads, bound to a channel and a durable file receipt."""
import asyncio
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from playwright.async_api import async_playwright, expect
from browser_session import connect

ROOT = Path('/data/output')


def save(path, state):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    temp.chmod(0o644)  # Public metadata only; no credentials or browser state.
    temp.replace(path)


def public_readback(state):
    params = urllib.parse.urlencode({'url': state['url'], 'format': 'json'})
    proxy = urllib.request.ProxyHandler({'https': 'http://127.0.0.1:17891'})
    with urllib.request.build_opener(proxy).open('https://www.youtube.com/oembed?' + params, timeout=30) as r:
        data = json.load(r)
    expected = state['author_url'].rstrip('/').lower()
    if data['author_url'].rstrip('/').lower() != expected or data['title'] != state['title']:
        raise RuntimeError('YouTube public readback has a different author or title')
    state.update(public_readback={'author_name': data['author_name'], 'author_url': data['author_url'],
                                 'title': data['title']}, verified_at=time.time())
    return state


async def await_public(state, timeout=90):
    deadline=time.monotonic()+timeout
    while True:
        try:
            await asyncio.to_thread(public_readback,state)
            return
        except (urllib.error.URLError,TimeoutError,OSError):
            if time.monotonic()>=deadline:
                raise RuntimeError('Publication has no public readback yet; the saved video id will be reconciled') from None
            await asyncio.sleep(3)


async def ensure_channel(page, state):
    channel = state['channel_id']
    create=page.get_by_role('button',name=re.compile(r'^(Create|Создать)$'))
    if (f'/channel/{channel}' in page.url and await create.is_visible()
            and state['channel_title'] in await page.locator('body').inner_text()):
        return
    # Studio's permission-denied page has no account menu. Select the actual
    # signed-in YouTube account first; the two brands belong to different logins.
    await page.goto('https://www.youtube.com/account',wait_until='commit',timeout=30000)
    account=page.get_by_role('button',name=re.compile(r'^(Меню аккаунта|Account menu)$'))
    await account.wait_for(timeout=20000)
    await account.click()
    header=page.locator('ytd-active-account-header-renderer:visible')
    await header.wait_for(timeout=20000)
    active=await header.inner_text()
    handle=state['author_url'].rstrip('/').rsplit('/',1)[-1]
    if state['channel_title'] not in active or handle.lower() not in active.lower():
        await page.get_by_text(re.compile(r'^(Switch account|Сменить аккаунт)$')).click()
        row=page.locator('ytd-account-item-renderer:visible').filter(
            has=page.get_by_text(state['channel_title'],exact=True))
        await row.wait_for(timeout=20000)
        # The account selector sometimes leaves a background navigation pending.
        # The target Studio page below independently verifies the resulting owner.
        await row.click(no_wait_after=True)
        await page.locator('ytd-account-item-renderer:visible').first.wait_for(state='hidden',timeout=30000)
    else:
        await page.keyboard.press('Escape')
    await page.goto('https://studio.youtube.com/channel/'+channel,wait_until='commit',timeout=30000)
    await create.wait_for(timeout=60000)
    if f'/channel/{channel}' not in page.url or state['channel_title'] not in await page.locator('body').inner_text():
        raise RuntimeError('YouTube Studio selected a different channel')


async def publish_ready(page,state,receipt):
    state.update(status='awaiting_checks',desired_visibility='public');save(receipt,state)
    warning=page.get_by_role('button',name=re.compile(r'^(Publish anyway|Все равно опубликовать|Всё равно опубликовать)$'))
    if await warning.is_visible():
        await page.get_by_role('button',name=re.compile(r'^(Go back|Назад)$')).last.click()
    await expect(page.locator('body')).to_contain_text(re.compile(
        r'Checks complete\.\s*No issues found|Проверка завершена\.\s*(?:Нарушений не обнаружено|Нарушений не найдено|Нарушений нет|Проблем не найдено)',re.S),timeout=600000)
    public=page.get_by_role('radio',name=re.compile(r'^(Открытый доступ|Public)$'))
    await public.click()
    await expect(public).to_have_attribute('aria-checked','true')
    state.update(status='publishing',publish_requested_at=time.time(),containsSyntheticMedia=True)
    save(receipt,state)
    await page.get_by_role('button',name=re.compile(r'^(Опубликовать|Publish)$')).click()
    await await_public(state)
    state.update(status='published',published_at=time.time(),publication_time_basis='public readback')
    save(receipt,state)


# (text shown on the language button, accessible name of the option) per supported language
LANGUAGE_PATTERNS={'ru':(r'Русский|Russian',r'^(Русский|Russian)$'),'en':(r'English|Английский',r'^(English|Английский)( \(.*\))?$')}
# options tried first when the list holds regional variants (the button text itself is matched with the first pattern)
LANGUAGE_PREFERENCE={'en':('English','English (United States)')}


async def upload(job):
    raw = Path(job['file']).resolve()
    if ROOT not in raw.parents:
        raise ValueError('Upload file must belong to the media output directory')
    sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    folder = ROOT / 'studio_uploads'
    folder.mkdir(exist_ok=True)
    receipt = folder / (job['channel_id'] + '_' + sha + '.json')
    state = json.loads(receipt.read_text()) if receipt.exists() else {
        **job, 'sha256': sha, 'status': 'new', 'started_at': time.time()}
    if state['sha256'] != sha or state['channel_id'] != job['channel_id'] or state['title'] != job['title']:
        raise RuntimeError('Existing upload receipt differs from this job')
    state['author_url'] = job['author_url']
    if state['status'] in ('published','publishing'):
        try:
            public_readback(state)
        except urllib.error.HTTPError as exc:
            if state['status']=='published' or exc.code not in (400,403,404):raise
        else:
            state.update(status='published',published_at=state.get('published_at',time.time()),
                publication_time_basis='public readback')
            save(receipt,state)
            return state
    with (ROOT / '.flow-generation.lock').open('a') as browser_lock:
        fcntl.flock(browser_lock, fcntl.LOCK_EX)
        async with async_playwright() as p:
            browser = await connect(p)
            try:
                context = browser.contexts[0]
                page = next((x for x in context.pages if 'studio.youtube.com' in x.url), None)
                if page is None:
                    page = await context.new_page()
                # A background tab never renders, so Playwright's "stable" check on it times out.
                await page.bring_to_front()
                page.set_default_timeout(20000)
                if state['status'] in ('uploaded_private','awaiting_checks','publishing') and state.get('id') and state.get('original_audio_language')=='ru':
                    # A normal YouTube check can take over five minutes. Resume
                    # the same saved upload after a bounded wait, without trying
                    # to re-enter Details into a Visibility-only dialog.
                    link=page.get_by_role('link',name=re.compile(re.escape(state['id'])))
                    await link.wait_for(timeout=10000)
                    public=page.get_by_role('radio',name=re.compile(r'^(Открытый доступ|Public)$'))
                    for _ in range(3):
                        if await public.is_visible():break
                        await page.get_by_role('button',name=re.compile(r'^(Далее|Next)$')).click()
                    await publish_ready(page,state,receipt)
                    await page.close()
                    return state
                if state['status'] == 'new':
                    await ensure_channel(page, state)
                    select_files=page.get_by_role('button',name=re.compile(r'^(Select files|Выбрать файлы)$'))
                    # A prior failure can leave this empty upload dialog open.
                    # Reuse its explicit chooser instead of clicking the covered
                    # dashboard Create button or relying on a hidden file input.
                    if not await select_files.is_visible():
                        # Scoped to the open menu: an empty channel shows this text on its dashboard too. A reused tab can still
                        # have the menu open from a failed attempt, and then the menu covers the Create button.
                        upload=page.locator('tp-yt-paper-dialog:visible').get_by_text(re.compile(r'^(Upload videos|Добавить видео|Загрузить видео)$'),exact=True).first
                        if not await upload.is_visible():
                            await page.get_by_role('button',name=re.compile(r'^(Create|Создать)$')).click()
                            await upload.wait_for(timeout=15000)
                        await upload.click()
                    await select_files.wait_for()
                    alias = folder / (job['brand'] + '_' + sha[:16] + '.mp4')
                    if not alias.exists():
                        os.link(raw, alias)
                    state.update(status='uploading', file=str(alias))
                    save(receipt, state)
                    async with page.expect_file_chooser() as chooser:
                        await select_files.click()
                    state['upload_selection_attempted_at']=time.time();save(receipt,state)
                    await (await chooser.value).set_files(str(alias),timeout=60000)
                elif not state.get('id'):
                    # Recover only the same live upload. Never submit the file twice after an uncertain response.
                    if Path(state['file']).stem.replace('_', ' ') not in await page.locator('body').inner_text():
                        raise RuntimeError('Uncertain upload has no live matching dialog; reconcile the saved private video')
                links = page.get_by_role('link').filter(has_text=re.compile(r'^https://youtube.com/shorts/'))
                await links.first.wait_for(timeout=180000)
                url = await links.first.get_attribute('href')
                video_id = urllib.parse.urlparse(url).path.rsplit('/', 1)[-1]
                if not re.fullmatch(r'[a-zA-Z0-9_-]{11}', video_id) or (state.get('id') and state['id'] != video_id):
                    raise RuntimeError('Upload dialog belongs to another video')
                state.update(id=video_id, url='https://youtu.be/' + video_id, status='uploaded_private')
                save(receipt, state)
                await page.get_by_role('textbox', name=re.compile(r'^(Укажите название|Add a title)')).fill(job['title'])
                await page.get_by_role('textbox', name=re.compile(r'^(Расскажите, о чем|Tell viewers about)')).fill(job['description'])
                await page.get_by_role('radio', name=re.compile(r'^(Нет, это видео не для детей|No, it.s not made for kids)$')).click()
                more = page.get_by_text(re.compile(r'^(Развернуть|Show more)$'), exact=True)
                if await more.is_visible():
                    await more.click()
                ai = page.get_by_role('radio', name=re.compile(r'^(Да, ИИ использовался|Yes, AI was used)$'))
                await ai.click()
                await expect(ai).to_have_attribute('aria-checked', 'true')
                tags = page.get_by_role('textbox', name=re.compile(r'^(Теги|Tags)$'))
                await tags.fill(','.join(job['tags']))
                await tags.press('Enter')
                lang_code=job.get('language','ru')
                shown,option=LANGUAGE_PATTERNS[lang_code]
                language=page.get_by_role('button',name=re.compile(r'^(Язык видео|Video language) ')).last
                await language.wait_for()
                if not re.search(shown,await language.inner_text()):
                    await language.click()
                    choices=page.get_by_role('option',name=re.compile(option))
                    names=[re.sub(r'\s+',' ',n).strip() for n in await choices.all_inner_texts()]
                    pick=next((names.index(n) for n in LANGUAGE_PREFERENCE.get(lang_code,()) if n in names),0)  # plain 'English' beats a regional variant
                    await choices.nth(pick).click()
                await expect(language).to_contain_text(re.compile(shown))
                state['original_audio_language']=lang_code
                save(receipt,state)
                for _ in range(3):
                    await page.get_by_role('button', name=re.compile(r'^(Далее|Next)$')).click()
                await publish_ready(page,state,receipt)
                await page.close()
            finally:
                await browser.close()
    return state


if __name__ == '__main__':
    os.umask(0o022)
    payload = json.loads(Path(sys.argv[1]).read_text())
    digest = hashlib.sha256(Path(payload['file']).read_bytes()).hexdigest()
    key = payload['channel_id'] + '_' + digest
    with (ROOT / ('.studio-upload-' + key + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = asyncio.run(upload(payload))
    print(json.dumps({'status': 'success', 'url': result['url'], 'id': result['id'],
                      'channel_id': result['channel_id'], 'sha256': result['sha256']}, ensure_ascii=False))
