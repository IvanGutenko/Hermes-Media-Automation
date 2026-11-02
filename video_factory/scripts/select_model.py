import asyncio
from playwright.async_api import async_playwright

async def select_option():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        flow_page = [x for x in context.pages if "flow.google.com" in x.url][0]
        await flow_page.bring_to_front()
        
        # Click "Switch to Veo 3.1 - Lite (supports character reference images)"
        lite_btn = flow_page.locator("text='Switch to Veo 3.1 - Lite', [role='radio']:has-text('Lite')").first
        if await lite_btn.count() > 0:
            print("Clicking 'Switch to Veo 3.1 - Lite'...")
            await lite_btn.click(force=True)
            await flow_page.wait_for_timeout(3000)
            
            await flow_page.screenshot(path="/data/output/flow_after_model_selected.png")
            print("Screenshot saved: flow_after_model_selected.png")
            
            tiles = await flow_page.locator("flow-grid-tile-container").all()
            print(f"Total tiles: {len(tiles)}")
            for i, t in enumerate(tiles):
                print(f"Tile {i} text: {await t.inner_text()}")
        else:
            print("Lite option not found")

asyncio.run(select_option())
