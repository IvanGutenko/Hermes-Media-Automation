"""Publish a single photo post through the owner's normal VK composer."""
import asyncio,fcntl,hashlib,json,re,sys,time
from pathlib import Path
from playwright.async_api import async_playwright,expect
from browser_session import connect
from vk_post_readback import normalized,without_explicit_links,explicit_links,target
from collections import Counter

ROOT=Path('/data/output')


def save(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));tmp.chmod(0o644);tmp.replace(path)


async def caption_text(node,text):
    remaining=Counter(target(u) for u in explicit_links(text));remove=[]
    links=await node.locator('a[href]').evaluate_all('(es)=>es.map(e=>e.href)')
    for i in reversed(range(len(links))):
        url=target(links[i])
        if remaining[url]>0:remove.append(i);remaining[url]-=1
    if any(remaining.values()):return None
    actual=await node.evaluate('''(node,indices)=>{
      const c=node.cloneNode(true);c.querySelectorAll('a[href]').forEach((a,i)=>{if(indices.includes(i))a.remove()});
      c.querySelectorAll('img.emoji[alt]').forEach(i=>i.replaceWith(document.createTextNode(i.alt)));
      const walk=document.createTreeWalker(c,NodeFilter.SHOW_TEXT),parts=[];
      while(walk.nextNode())parts.push(walk.currentNode.textContent);return parts.join(' ');
    }''',remove)
    return normalized(actual)


async def matches(node,text):
    return await caption_text(node,text)==normalized(without_explicit_links(text))


async def discover(page,job,state):
    group=job['group_id']
    await page.get_by_test_id('post').first.wait_for(timeout=25000)
    nodes=page.get_by_test_id('post_text');found=set();observed={};candidates=set()
    expected=normalized(without_explicit_links(job['text']))
    for scroll in range(13):
        for node in await nodes.all():
            identity=await node.get_attribute('data-post-id')
            text=await node.inner_text()
            if identity:observed[identity]=text[:1000]
            if identity and re.fullmatch('-'+str(group)+r'_\d+',identity):
                actual=await caption_text(node,job['text'])
                # VK shortens displayed URL labels and can append an expand
                # control. Select candidates using link-normalized prose, then
                # require the complete exact caption on the permalink below.
                if actual and actual.startswith(expected[:80]):candidates.add(identity)
                if actual==expected:found.add(identity)
        if found or scroll==12:break
        await page.mouse.wheel(0,1100);await asyncio.sleep(0.6)
    # Feed cards can omit the tail/link destinations until expanded. Verify a
    # candidate on its own permalink before accepting any part of its caption.
    if not found:
        for identity in sorted(candidates):
            await page.goto('https://vk.ru/wall'+identity,wait_until='domcontentloaded',timeout=45000)
            node=page.locator('[data-testid="post_text"][data-post-id="'+identity+'"]')
            await node.wait_for(timeout=45000)
            if await matches(node,job['text']):found.add(identity)
    if len(found)>1:raise RuntimeError('VK has several matching posts; no new post sent')
    if found:
        identity=next(iter(found));state.update(id=int(identity.split('_')[-1]),url='https://vk.ru/wall'+identity)
        return True
    save(ROOT/('native_vk_photo_'+job['key']+'.readback.json'),{'observed':observed,'expected_text':job['text']})
    return False


async def verify_photo(page,job,state):
    url='https://vk.ru/wall-'+str(job['group_id'])+'_'+str(state['id'])
    if page.url.split('?')[0]!=url:
        await page.goto(url,wait_until='domcontentloaded',timeout=45000)
    node=page.locator(f'[data-testid="post_text"][data-post-id="-{job["group_id"]}_{state["id"]}"]')
    await node.wait_for(timeout=45000)
    if not await matches(node,job['text']):raise RuntimeError('Published VK caption differs from the reviewed post')
    photo=await node.evaluate('''node=>{
      for(let p=node.parentElement;p && p!==document.body;p=p.parentElement){
        if(p.querySelectorAll('[data-testid="post_text"]').length!==1)break;
        const media=[...p.querySelectorAll('img:not(.emoji)')].filter(i=>i.complete && i.naturalWidth>500);
        const links=[...p.querySelectorAll('a[href]')].filter(a=>/\\/photo-?\\d+_\\d+/.test(a.href));
        if(media.length || links.length)return {images:media.map(i=>({src:i.src,width:i.naturalWidth,height:i.naturalHeight})),links:links.map(a=>a.href)};
      } return null;
    }''')
    if not photo:raise RuntimeError('VK post id saved, but its attached image is not verified yet')
    state.update(status='published',public_verified_at=time.time(),photo_readback=photo,
        verification='Exact community/post id, complete caption/action links and attached photo in the same post container')


async def editor_text(box):
    return await box.evaluate('''node=>{
      const c=node.cloneNode(true);c.querySelectorAll('img.emoji[alt]').forEach(i=>i.replaceWith(document.createTextNode(i.alt)));
      const walk=document.createTreeWalker(c,NodeFilter.SHOW_TEXT),parts=[];
      while(walk.nextNode())parts.push(walk.currentNode.textContent);return parts.join(' ');
    }''')


async def clear_published_draft(page,box,group):
    current=normalized(await editor_text(box))
    for path in ROOT.glob('native_vk_photo_*.json'):
        if not re.fullmatch(r'native_vk_photo_[a-f0-9]{64}\.json',path.name):continue
        old=json.loads(path.read_text())
        if (old.get('status')!='published' or old.get('group_id')!=group or not old.get('public_verified_at')
                or normalized(old.get('text',''))!=current):continue
        # The full draft is already published and independently read back. It is
        # an agent-created residual, not an unknown owner draft.
        await page.keyboard.press('Escape')
        await page.get_by_role('button',name=re.compile(r'Выйти\s+без\s+сохранения')).click(timeout=15000)
        await box.wait_for(state='hidden')
        old.update(residual_draft_discarded_at=time.time());save(path,old)
        return True
    return False


async def publish(job_path,preview=False):
    job=json.loads(Path(job_path).read_text());image=Path(job['image']).resolve()
    if ROOT not in image.parents or hashlib.sha256(image.read_bytes()).hexdigest()!=job['sha256']:raise ValueError('VK image differs from its job')
    receipt=ROOT/('native_vk_photo_'+job['key']+'.json')
    state=json.loads(receipt.read_text()) if receipt.exists() else {**job,'status':'new','started_at':time.time()}
    if state['text']!=job['text'] or state['sha256']!=job['sha256']:raise RuntimeError('VK post receipt identity changed')
    if state['status']=='published':return state
    with (ROOT/'.flow-generation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        async with async_playwright() as p:
            b=await connect(p);page=await b.contexts[0].new_page()
            try:
                page.set_default_timeout(25000)
                await page.goto('https://vk.ru/club'+str(job['group_id']),wait_until='domcontentloaded',timeout=45000)
                await page.get_by_role('heading',name=job.get('group_title','ThinkScope'),exact=True,level=1).wait_for(timeout=45000)
                if state.get('status')=='publishing':
                    if not state.get('id') and not await discover(page,job,state):raise RuntimeError('VK send is uncertain; no replacement submitted')
                else:
                    await page.get_by_role('button',name='Создать',exact=True).click()
                    await page.get_by_role('dialog').get_by_text('Пост',exact=True).click()
                    box=page.get_by_test_id('posting_base_screen_input_message');await box.wait_for()
                    current=normalized(await editor_text(box))
                    if current and current!=normalized(job['text']):
                        if not await clear_published_draft(page,box,job['group_id']):
                            raise RuntimeError('VK composer has another unfinished draft; preserved')
                        await page.get_by_role('button',name='Создать',exact=True).click()
                        await page.get_by_role('dialog').get_by_text('Пост',exact=True).click()
                        await box.wait_for()
                        if normalized(await editor_text(box)):raise RuntimeError('VK restored a different draft; preserved')
                    same_text_draft=bool(current) and current==normalized(job['text'])
                    await box.fill(job['text'])
                    if normalized(await box.inner_text())!=normalized(job['text']):raise RuntimeError('VK did not retain the exact post text')
                    upload=page.get_by_test_id('posting_base_screen_download_from_device')
                    if same_text_draft and not await upload.count() and state.get('image_uploaded_sha256')!=job['sha256']:
                        # A crashed earlier attempt left this very post (identical text) in the composer with an image whose origin
                        # no receipt can prove. Nobody else types these exact words, so it is our own residue: discard it and rebuild
                        # the draft from the reviewed file instead of blocking every later VK post (stuck since 2026-10-30).
                        await page.keyboard.press('Escape')
                        await page.get_by_role('button',name=re.compile(r'Выйти\s+без\s+сохранения')).click(timeout=15000)
                        await box.wait_for(state='hidden')
                        await page.get_by_role('button',name='Создать',exact=True).click()
                        await page.get_by_role('dialog').get_by_text('Пост',exact=True).click()
                        await box.wait_for()
                        if normalized(await editor_text(box)):raise RuntimeError('VK restored the discarded draft; preserved')
                        await box.fill(job['text'])
                        if normalized(await box.inner_text())!=normalized(job['text']):raise RuntimeError('VK did not retain the exact post text')
                        upload=page.get_by_test_id('posting_base_screen_download_from_device')
                    if await upload.count():
                        input_handle=await upload.element_handle()
                        state.update(status='preparing',upload_started_at=time.time());save(receipt,state)
                        await upload.set_input_files(str(image),timeout=60000)
                        if await input_handle.evaluate('(el)=>el.files[0]?.name')!=image.name:raise RuntimeError('VK selected a different image')
                        state['image_uploaded_sha256']=job['sha256'];save(receipt,state)
                    elif state.get('image_uploaded_sha256')!=job['sha256']:
                        raise RuntimeError('VK draft contains an uncorrelated image; preserved')
                    await page.get_by_text('Далее',exact=True).click()
                    await page.wait_for_timeout(1000)
                    save(receipt.with_suffix('.ui.json'),{'dialogs':await page.locator('[role=dialog]').evaluate_all('(es)=>es.map(e=>({label:e.getAttribute("aria-label"),text:e.innerText}))'),
                        'buttons':await page.locator('button').evaluate_all('(es)=>es.filter(e=>e.getClientRects().length).map(e=>({text:e.innerText,label:e.getAttribute("aria-label")})).slice(-25)')})
                    if preview:
                        state.update(status='draft_preview');save(receipt,state);return state
                    story=page.locator('label').filter(has_text=re.compile(r'Опубликовать\s+в\s+истории')).first
                    if await story.count():
                        checkbox=story.locator('input[type=checkbox]')
                        if await checkbox.count() and await checkbox.is_checked():await story.click()
                    state.update(status='publishing',publish_requested_at=time.time());save(receipt,state)
                    await page.get_by_role('button',name='Опубликовать',exact=True).click()
                    await page.get_by_text('Опубликовать',exact=True).wait_for(state='hidden',timeout=60000)
                    await page.reload(wait_until='domcontentloaded',timeout=45000)
                    if not await discover(page,job,state):raise RuntimeError('Submitted VK post is not visible yet; no replacement sent')
                    save(receipt,state)
                await verify_photo(page,job,state);save(receipt,state)
                return state
            except Exception:
                await page.screenshot(path=str(receipt.with_suffix('.failure.png')))
                save(receipt.with_suffix('.failure_ui.json'),{'url':page.url,'dialogs':await page.locator('[role=dialog]').evaluate_all('(es)=>es.map(e=>({label:e.getAttribute("aria-label"),text:e.innerText}))')})
                raise
            finally:await page.close();await b.close()


if __name__=='__main__':print(json.dumps(asyncio.run(publish(sys.argv[1],'--preview' in sys.argv)),ensure_ascii=False))
