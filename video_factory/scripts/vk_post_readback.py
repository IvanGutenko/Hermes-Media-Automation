"""Read a specific wall post with the owner's saved browser session."""
import asyncio
from collections import Counter
import fcntl
import hashlib
import json
import re
import sys
import time
from urllib.parse import urlparse,parse_qs,urldefrag
from pathlib import Path
from playwright.async_api import async_playwright,TimeoutError as PlaywrightTimeoutError
from browser_session import connect


def normalized(text):
    return re.sub(r'\s+([,.;:!?])',r'\1',' '.join(text.split()))


def explicit_links(text):
    return [m.group(0).rstrip('.,;:!?)]') for m in re.finditer(r'https?://\S+',text)]


def without_explicit_links(text):
    def remove(match):
        raw=match.group(0);url=raw.rstrip('.,;:!?)]')
        return raw[len(url):]
    return re.sub(r'https?://\S+',remove,text)


def target(url):
    parsed=urlparse(url)
    if parsed.hostname in ('vk.ru','vk.com','away.vk.ru','away.vk.com'):
        url=parse_qs(parsed.query).get('to',[url])[0]
    return urldefrag(url)[0].rstrip('/')


async def verify(job):
    group=int(job['group_id']);post=int(job['id'])
    with Path('/data/output/.flow-generation.lock').open('a') as lock:
        # Research can retry later; do not occupy a waiting process while a video
        # generator or another publisher owns the persistent browser.
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        async with async_playwright() as p:
            browser=await connect(p);page=None
            try:
                page=await browser.contexts[0].new_page()
                await page.goto(f'https://vk.ru/wall-{group}_{post}',wait_until='domcontentloaded',timeout=30000)
                node=page.locator(f'[data-testid="post_text"][data-post-id="-{group}_{post}"]')
                try:await node.wait_for(timeout=12000)
                except PlaywrightTimeoutError:
                    # The standalone wall route can fail loading its JS chunk
                    # while the same post renders on the owning community page.
                    await page.goto(f'https://vk.ru/club{group}',wait_until='domcontentloaded',timeout=30000)
                    await node.wait_for(timeout=25000)
                remaining=Counter(target(u) for u in explicit_links(job['text']))
                links=await node.locator('a[href]').evaluate_all('(els)=>els.map(a=>a.href)')
                remove=[]
                # VK shortens URL display text. Remove only the corresponding explicit
                # URL anchors, keeping library names such as Three.js and bare labels.
                for i in reversed(range(len(links))):
                    url=target(links[i])
                    if remaining[url]>0:remove.append(i);remaining[url]-=1
                if any(remaining.values()):raise RuntimeError('VK action links differ from the expected destinations')
                actual=await node.evaluate('''(node,indices)=>{
                  const copy=node.cloneNode(true);copy.querySelectorAll('a[href]').forEach((a,i)=>{if(indices.includes(i))a.remove()});
                  copy.querySelectorAll('img.emoji[alt]').forEach(img=>img.replaceWith(document.createTextNode(img.alt)));
                  const walk=document.createTreeWalker(copy,NodeFilter.SHOW_TEXT),parts=[];
                  while(walk.nextNode())parts.push(walk.currentNode.textContent);return parts.join(' ');
                }''',remove)
                if normalized(actual)!=normalized(without_explicit_links(job['text'])):
                    raise RuntimeError('VK readback differs from the expected post text')
                return {'verified_at':time.time(),'verification':'Owning wall/post id, normalized visible body and all action link targets',
                        'text_sha256':hashlib.sha256(normalized(actual).encode()).hexdigest()}
            finally:
                if page:await page.close()
                await browser.close()


if __name__=='__main__':
    path=Path(sys.argv[1]).resolve()
    if Path('/data/output') not in path.parents:raise ValueError('Readback job outside output')
    print(json.dumps(asyncio.run(verify(json.loads(path.read_text())))))
