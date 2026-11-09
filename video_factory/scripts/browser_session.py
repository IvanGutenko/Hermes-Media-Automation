"""Bounded connection retries for the persistent, signed-in server browser."""
import asyncio
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


async def hide_heavy_tabs(context):
    """Put a static signed-in page on top so a kept Flow project tab stops rendering. Never opens or closes anything."""
    for page in context.pages:
        if page.url.startswith('https://myaccount.google.com'):
            await page.bring_to_front()
            return True
    return False


async def set_frozen(page, frozen):
    """Suspend a retained editor without discarding its pending result or DOM."""
    session = await asyncio.wait_for(page.context.new_cdp_session(page), 5)
    try:
        await asyncio.wait_for(session.send('Page.setWebLifecycleState',
            {'state': 'frozen' if frozen else 'active'}), 5)
    finally:
        await asyncio.wait_for(session.detach(), 5)


async def connect(playwright):
    from browser_tabs import http_tidy,HTTP_BUDGET
    try:await asyncio.wait_for(asyncio.to_thread(http_tidy),HTTP_BUDGET+5)
    except Exception:pass
    for attempt in range(3):
        try:
            browser = await playwright.chromium.connect_over_cdp(
                'http://127.0.0.1:9222', timeout=20000)
            # A killed worker cannot run its finally/page.close. Recover its
            # disposable dashboards before the next job adds more renderers.
            from browser_tabs import tidy
            try:await asyncio.wait_for(tidy(browser.contexts[0]),20)
            except Exception:pass
            return browser
        except PlaywrightTimeoutError:
            if attempt == 2:
                raise
            await asyncio.sleep(2 * (attempt + 1))
