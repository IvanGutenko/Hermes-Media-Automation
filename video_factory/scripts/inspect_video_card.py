import asyncio
from playwright.async_api import async_playwright

async def inspect_and_download():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        flow_page = [x for x in context.pages if "flow.google.com" in x.url][0]
        await flow_page.bring_to_front()
        
        # Click on the video tile (first tile)
        video_tile = flow_page.locator("flow-grid-tile-container").first
        print("Clicking on video tile...")
        await video_tile.click()
        await flow_page.wait_for_timeout(2000)
        
        await flow_page.screenshot(path="/data/output/flow_video_clicked.png")
        print("Screenshot saved: flow_video_clicked.png")
        
        # Check for download button or video tag
        videos = await flow_page.locator("video").all()
        print(f"Video elements found: {len(videos)}")
        for i, v in enumerate(videos):
            src = await v.get_attribute("src")
            print(f"Video {i} src: {src}")
            
        # Check all buttons with aria-label or text containing download or export
        action_btns = await flow_page.locator("button[aria-label*='Download' i], button[aria-label*='Export' i], button:has-text('Download'), [aria-label*='More' i]").all()
        print(f"Action buttons: {len(action_btns)}")
        for b in action_btns:
            aria = await b.get_attribute("aria-label")
            text = await b.inner_text()
            print(f"Button: aria='{aria}', text='{text}'")

asyncio.run(inspect_and_download())
