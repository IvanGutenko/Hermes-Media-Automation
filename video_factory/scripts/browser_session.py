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


async def connect(playwright):
    for attempt in range(3):
        try:
            return await playwright.chromium.connect_over_cdp(
                'http://127.0.0.1:9222', timeout=20000)
        except PlaywrightTimeoutError:
            if attempt == 2:
                raise
            await asyncio.sleep(2 * (attempt + 1))
