#!/usr/bin/env python3
"""Move the TikTok account signed in in the factory browser to its own stored session.

TikTok's web site cannot hold two accounts, so a second own account lives outside the browser profile. The session is
stored only after it has proved itself in an isolated context, and only then are TikTok's cookies removed from the
profile, so that the owner can sign the profile's own account back in. Removing cookies is not a logout: the stored
session stays valid.

Usage (inside the container): tiktok_session.py <handle>
"""
import asyncio
import fcntl
import re
import sys
from playwright.async_api import async_playwright
from browser_session import connect, hide_heavy_tabs
from tiktok_publisher import ROOT, close_account_context, ensure_signed_in

TIKTOK=re.compile(r'(^|\.)tiktok\.com$')


def only_tiktok(state):
    return {'cookies':[c for c in state['cookies'] if TIKTOK.search(c['domain'].lstrip('.'))],
            'origins':[o for o in state['origins'] if TIKTOK.search(o['origin'].split('//')[-1])]}


async def move(handle):
    with (ROOT/'.flow-generation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        async with async_playwright() as p:
            browser=await connect(p);main=browser.contexts[0]
            try:
                state=only_tiktok(await main.storage_state())
                context=await browser.new_context(storage_state=state)
                page=await context.new_page()
                await ensure_signed_in(page,handle)  # raises unless this exact account answers from the isolated context
                await page.close()
                await close_account_context(context,handle)
                for domain in sorted({c['domain'] for c in state['cookies']}):
                    await main.clear_cookies(domain=domain)
                await hide_heavy_tabs(main)
                left=[c['name'] for c in await main.cookies('https://www.tiktok.com') if c['name'] in ('sessionid','sid_tt')]
                print({'stored':handle,'cookies':len(state['cookies']),'session_left_in_profile':left})
            finally:await browser.close()


if __name__=='__main__':
    asyncio.run(move(sys.argv[1]))
