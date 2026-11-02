#!/usr/bin/env python3
"""Read-only: is the factory browser signed in to Instagram, and as which account? Posts nothing."""
import asyncio
import fcntl
import json
import re
from pathlib import Path
from playwright.async_api import async_playwright
from browser_session import connect

ROOT=Path('/data/output')
RESERVED={'reels','direct','explore','accounts','stories','p','reel','about','legal'}


async def status():
    with (ROOT/'.flow-generation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        async with async_playwright() as p:
            browser=await connect(p)
            try:
                page=await browser.contexts[0].new_page()
                try:
                    await page.goto('https://www.instagram.com/',wait_until='domcontentloaded',timeout=60000)
                    await page.wait_for_timeout(8000)
                    if await page.locator('input[name=email],input[name=username],input[name=pass]').count():
                        return {'signed_in':False}
                    # The signed-in sidebar is icons only; its profile entry is the lone /<handle>/ link on the left edge.
                    hrefs=await page.evaluate("""()=>[...document.querySelectorAll('a[href]')]
                        .filter(e=>e.getBoundingClientRect().x<90).map(e=>e.getAttribute('href'))""")
                    handles=[h.strip('/') for h in hrefs if re.fullmatch(r'/[A-Za-z0-9_.]{1,30}/',h or '')
                        and h.strip('/') not in RESERVED]
                    return {'signed_in':bool(handles),'handle':handles[0] if handles else None}
                finally:await page.close()
            finally:await browser.close()


if __name__=='__main__':
    print(json.dumps(asyncio.run(status())))
