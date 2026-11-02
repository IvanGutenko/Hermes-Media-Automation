import asyncio
from playwright.async_api import async_playwright

async def set_default_veo_lite():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        flow_page = [x for x in context.pages if "flow.google.com" in x.url][0]
        await flow_page.bring_to_front()
        
        # Click Veo 3.1 - Lite option
        lite_opt = flow_page.locator("*:has-text('Veo 3.1 - Lite')").last
        await lite_opt.click(force=True)
        await flow_page.wait_for_timeout(500)
        
        # Click Save button
        save_btn = flow_page.locator("button:has-text('Save')")
        await save_btn.click(force=True)
        print("Clicked Save!")
        await flow_page.wait_for_timeout(1000)
        
        # Close settings
        close_btn = flow_page.locator("button[aria-label='Close'], button:has-text('close')")
        if await close_btn.count() > 0:
            await close_btn.first.click(force=True)
            
        await flow_page.screenshot(path="/data/output/flow_settings_saved_lite.png")
        print("Settings saved with Veo 3.1 - Lite!")

asyncio.run(set_default_veo_lite())
