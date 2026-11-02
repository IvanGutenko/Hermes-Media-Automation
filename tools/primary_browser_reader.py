"""Read public primary articles that require a normal browser to render."""
import asyncio
import fcntl
import hashlib
import json
import re
import sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from browser_session import connect

from primary_publishers import primary_host


def allowed(url):
    p=urlparse(url);host=(p.hostname or '').lower()
    return p.scheme=='https' and not p.username and primary_host(host)


async def read(url):
    if not allowed(url):raise ValueError('Only configured public primary publishers are permitted')
    with Path('/data/output/.flow-generation.lock').open('a') as lock:
        # Research yields to video jobs; no waiting orphan browser clients.
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        async with async_playwright() as p:
            browser=await connect(p);page=None
            try:
                page=await browser.contexts[0].new_page()
                await page.goto(url,wait_until='domcontentloaded',timeout=45000)
                if not allowed(page.url):raise ValueError('Primary page redirected to a different publisher')
                await page.wait_for_function('document.body && document.body.innerText.length > 300',timeout=20000)
                text=await page.evaluate('''() => {
                  const root=document.querySelector('article') || document.querySelector('main') || document.body;
                  const walk=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),parts=[];
                  while(walk.nextNode()) {
                    const n=walk.currentNode,p=n.parentElement;
                    if(p && !p.closest('script,style,noscript,header,footer,nav') && p.getClientRects().length
                        && getComputedStyle(p).visibility!=='hidden' && n.textContent.trim()) parts.push(n.textContent.trim());
                  }
                  return parts.join(' ');
                }''')
                text=' '.join(text.split())[:24000]
                if len(text)<120 or re.search(r'Access denied|403 Forbidden|Just a moment|Checking your browser|подтвердите.{0,40}человек|капча',text,re.I):
                    raise RuntimeError('Primary page requires access confirmation; no challenge bypass')
                return {'url':url,'text':text,'sha256':hashlib.sha256(text.encode()).hexdigest(),
                        'fetched_at':datetime.now(timezone.utc).isoformat(),'retrieved_via':'server_browser','extractor_version':2}
            finally:
                if page:await page.close()
                await browser.close()


if __name__=='__main__':
    print(json.dumps(asyncio.run(read(sys.argv[1])),ensure_ascii=False))
