import asyncio
from playwright.async_api import async_playwright

async def click_item():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        flow_page = [x for x in context.pages if "flow.google.com" in x.url][0]
        await flow_page.bring_to_front()
        
        opt = flow_page.locator("div:has-text('Switch to Veo 3.1 - Lite')").last
        print("Clicking 'Switch to Veo 3.1 - Lite'...")
        await opt.click(force=True)
        await flow_page.wait_for_timeout(3000)
        
        await flow_page.screenshot(path="/data/output/flow_after_clicking_veo_lite.png")
        print("Screenshot saved: flow_after_clicking_veo_lite.png")
        
        # Check what appears
        panel_text = await flow_page.locator(".agent-panel-wrapper").inner_text()
        print("Panel text snippet:")
        print(panel_text[-300:])

asyncio.run(click_item())
