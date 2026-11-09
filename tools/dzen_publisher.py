"""Publish a reviewed article to the owner's persisted Dzen session, once per document."""
import asyncio
import base64
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
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2))
    temp.chmod(0o644);temp.replace(path)


def plain(article):
    return article['intro']+'\n\n'+'\n\n'.join(s['heading']+'\n'+s['text'] for s in article['sections'])+\
        '\n\nБольше разборов о переезде и путешествиях: https://t.me/Migratoriya'


def normalized(text):
    return ' '.join(text.split())


async def read_public(browser,state,article):
    identity=state['publication_id']
    short=base64.urlsafe_b64encode(bytes.fromhex(identity)).decode().rstrip('=')
    context=await browser.new_context()
    try:
        page=await context.new_page()
        await page.goto('https://dzen.ru/a/'+short+'?is_autologin_ya=true',wait_until='domcontentloaded',timeout=45000)
        title=await page.get_by_test_id('article-title').inner_text(timeout=60000)
        links=await page.locator('a[href="/id/'+state['channel_id']+'"]').count()
        body=normalized(await page.locator('body').inner_text())
        if (title!=article['title'] or not links or normalized(article['intro']) not in body
                or any(normalized(s['text']) not in body for s in article['sections'])
                or 'https://t.me/Migratoriya' not in body):
            raise RuntimeError('Anonymous Dzen readback differs from the reviewed article or owner')
        state.update(status='published',url='https://dzen.ru/a/'+short,public_verified_at=time.time(),
            verification='Anonymous browser: exact title, body sections and owning channel link')
    finally:
        await context.close()


async def publish(folder,wait_browser_seconds=0):
    folder=Path(folder).resolve()
    if ROOT not in folder.parents:raise ValueError('Article must be in the media output directory')
    article=json.loads((folder/'article.json').read_text())
    if not article.get('review',{}).get('supported'):raise RuntimeError('No successful factual article review')
    from editorial_policy import allowed
    if not allowed(article['title']+' '+plain(article)):raise RuntimeError('Article excluded by owner topic policy')
    channel=article['target_channel'];receipt=folder/'dzen_receipt.json'
    state=json.loads(receipt.read_text()) if receipt.exists() else {'status':'new','channel_id':channel,'started_at':time.time()}
    if state['channel_id']!=channel:raise RuntimeError('Dzen receipt belongs to another channel')
    if state['status']=='published':return state
    with (ROOT/'.flow-generation.lock').open('a') as lock:
        deadline=time.monotonic()+wait_browser_seconds
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise
                await asyncio.sleep(1)
        async with async_playwright() as p:
            browser=await connect(p)
            page=None
            try:
                if state['status']=='publishing':
                    # Reconcile the same document. Never create a replacement after an uncertain publish.
                    await read_public(browser,state,article);save(receipt,state);return state
                context=browser.contexts[0]
                page=next((x for x in context.pages if state.get('editor_url') and state['editor_url'].split('?')[0] in x.url),None)
                if page is None:
                    page=await context.new_page()
                    await page.goto(state.get('editor_url','https://dzen.ru/profile/editor/id/'+channel),wait_until='domcontentloaded',timeout=45000)
                page.set_default_timeout(20000)
                if state['status']=='new':
                    await page.get_by_role('link',name='Смыслоскоп',exact=True).wait_for()
                    if '/profile/editor/id/'+channel not in page.url:raise RuntimeError('Dzen owner identity mismatch')
                    await page.get_by_test_id('add-publication-button').click()
                    await page.get_by_role('button',name='Написать статью',exact=True).click()
                    await page.wait_for_url(re.compile('/profile/editor/id/'+channel+r'/[a-f0-9]{24}/edit'),timeout=45000)
                    state.update(status='draft_created',editor_url=page.url.split('?')[0],publication_id=re.search(r'/([a-f0-9]{24})/edit',page.url).group(1))
                    save(receipt,state)
                else:
                    state.setdefault('publication_id',re.search(r'/([a-f0-9]{24})/edit',state['editor_url']).group(1))
                close=page.get_by_role('button',name='Закрыть',exact=True)
                if await close.is_visible():
                    await close.click();await close.wait_for(state='hidden')
                title=page.get_by_role('textbox').nth(0);body=page.get_by_role('textbox').nth(1)
                await body.wait_for(timeout=45000)
                expected=plain(article)
                if state['status'] in ('draft_created','entering_content'):
                    current=normalized(await body.inner_text())
                    if current and not normalized(expected).startswith(current):
                        raise RuntimeError('Draft contains unrecognized edits; it will not be overwritten')
                    state.update(status='entering_content',title=article['title']);save(receipt,state)
                    await title.fill(article['title']);await body.fill(article['intro'])
                    for section in article['sections']:
                        await page.keyboard.press('Enter');await page.keyboard.press('Enter')
                        await page.keyboard.press('Control+b');await page.keyboard.insert_text(section['heading'])
                        await page.keyboard.press('Control+b');await page.keyboard.press('Enter')
                        await page.keyboard.insert_text(section['text'])
                    await page.keyboard.press('Enter');await page.keyboard.press('Enter')
                    await page.keyboard.insert_text('Больше разборов о переезде и путешествиях: https://t.me/Migratoriya')
                    if normalized(await body.inner_text())!=normalized(expected):raise RuntimeError('Dzen did not retain the exact reviewed article')
                    state.update(status='content_entered',content_sha256=hashlib.sha256(expected.encode()).hexdigest());save(receipt,state)
                cover=folder/'cover.jpg'
                if not cover.exists():raise RuntimeError('Article has no own illustrative cover yet')
                if not state.get('cover_inserted'):
                    if state['status']=='cover_uploading':
                        await expect(page.get_by_test_id('article-publish-btn')).to_be_enabled(timeout=60000)
                    elif not await body.locator('img').count():
                        state['status']='cover_uploading';save(receipt,state)
                        await body.press('Control+Home');await body.press('Enter');await body.press('ArrowUp')
                        await page.locator('button[data-tip="Вставить изображение"]').click()
                        await page.locator('input[type=file]').first.set_input_files(str(cover))
                        await expect(page.get_by_test_id('article-publish-btn')).to_be_enabled(timeout=60000)
                    image=body.locator('img').first;await image.wait_for()
                    await image.scroll_into_view_if_needed()
                    await expect(image).to_have_js_property('complete',True,timeout=60000)
                    await expect(image).not_to_have_js_property('naturalWidth',0,timeout=60000)
                    if not await image.evaluate('(e)=>e.naturalWidth>0 && e.src.includes("dzeninfra.ru")'):
                        raise RuntimeError('Own article illustration has not finished uploading')
                    state.update(status='content_entered',cover_inserted=True,cover_sha256=hashlib.sha256(cover.read_bytes()).hexdigest());save(receipt,state)
                if normalized(expected) not in normalized(await body.inner_text()):raise RuntimeError('Draft body changed before publication')
                if await title.inner_text()!=article['title']:raise RuntimeError('Draft title changed before publication')
                await expect(page.get_by_test_id('article-publish-btn')).to_be_enabled()
                await page.get_by_test_id('article-publish-btn').click()
                await page.get_by_test_id('publish-btn').wait_for()
                state.update(status='publishing',publish_requested_at=time.time());save(receipt,state)
                await page.get_by_test_id('publish-btn').click()
                await read_public(browser,state,article);save(receipt,state)
                await page.close()
            except Exception as exc:
                state.update(last_error=type(exc).__name__+': '+str(exc)[-600:],checked_at=time.time());save(receipt,state)
                if page:
                    try:
                        snapshot=await asyncio.wait_for(page.locator('body').aria_snapshot(),timeout=5)
                        save(folder/'last_ui.json',{'url':page.url,'snapshot':snapshot})
                    except Exception:pass
                    # A recorded draft is resumed in its own tab. Without one the
                    # next attempt opens a fresh editor, so this tab would only pile up.
                    if not (state.get('editor_url') and state['editor_url'].split('?')[0] in page.url):
                        try:await asyncio.wait_for(page.close(),5)
                        except Exception:pass
                raise
            finally:
                await browser.close()
    return state


if __name__=='__main__':
    folder=Path(sys.argv[1]).resolve()
    if ROOT not in folder.parents:raise ValueError('Article must be in the media output directory')
    with (folder/'.dzen-publish.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        state=asyncio.run(publish(folder,300 if '--wait-browser' in sys.argv else 0))
    print(json.dumps({'status':state['status'],'url':state.get('url')},ensure_ascii=False))
