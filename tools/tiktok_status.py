#!/usr/bin/env python3
"""Read-only: is the factory browser signed in to TikTok, as which account, and is uploading offered?"""
import asyncio
import fcntl
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
from browser_session import connect

ROOT=Path('/data/output')


async def status():
    with (ROOT/'.flow-generation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        async with async_playwright() as p:
            browser=await connect(p)
            try:
                page=await browser.contexts[0].new_page()
                try:
                    # /profile sends a signed-in user to /@handle and a signed-out one to the login page.
                    await page.goto('https://www.tiktok.com/profile',wait_until='domcontentloaded',timeout=60000)
                    await page.wait_for_timeout(8000)
                    match=re.fullmatch(r'/@([A-Za-z0-9_.]{1,24})/?',urlsplit(page.url).path)
                    result={'signed_in':bool(match),'handle':match[1] if match else None,'url':page.url}
                    if match:
                        await page.goto('https://www.tiktok.com/tiktokstudio/upload',wait_until='domcontentloaded',timeout=60000)
                        await page.wait_for_timeout(8000)
                        result['upload_input']=await page.locator('input[type=file]').count()
                        result['upload_url']=page.url
                    return result
                finally:await page.close()
            finally:await browser.close()


if __name__=='__main__':
    print(json.dumps(asyncio.run(status())))
