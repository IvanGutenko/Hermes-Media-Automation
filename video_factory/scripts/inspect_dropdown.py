import asyncio
from playwright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        flow_page = [x for x in context.pages if "flow.google.com" in x.url][0]
        await flow_page.bring_to_front()
        
        tune_btn = flow_page.locator("flow-base-prompt-box button[aria-label='Settings']")
        await tune_btn.first.click(force=True)
        await flow_page.wait_for_timeout(1000)
        
        veo_el = flow_page.locator("*:has-text('Veo 3.1 - Quality')").last
        print("Veo element tag:", await veo_el.evaluate("e => e.tagName"))
        print("Veo element class:", await veo_el.evaluate("e => e.className"))
        print("Clicking Veo element...")
        await veo_el.click(force=True)
        await flow_page.wait_for_timeout(1000)
        
        await flow_page.screenshot(path="/data/output/flow_dropdown_opened.png")
        print("Screenshot saved: flow_dropdown_opened.png")

asyncio.run(inspect())
