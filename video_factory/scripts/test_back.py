import asyncio
from playwright.async_api import async_playwright

async def back_to_project():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        flow_page = [x for x in context.pages if "flow.google.com" in x.url][0]
        await flow_page.bring_to_front()
        
        done_btn = flow_page.locator("button:has-text('Done')")
        if await done_btn.count() > 0:
            print("Clicking Done button...")
            await done_btn.first.click(force=True)
            await flow_page.wait_for_timeout(2000)
        else:
            back_btn = flow_page.locator("button[aria-label='Back'], [aria-label*='back' i]").first
            if await back_btn.count() > 0:
                print("Clicking back button...")
                await back_btn.click(force=True)
                await flow_page.wait_for_timeout(2000)
                
        await flow_page.screenshot(path="/data/output/flow_back_in_project.png")
        print("Screenshot saved: flow_back_in_project.png")

asyncio.run(back_to_project())
