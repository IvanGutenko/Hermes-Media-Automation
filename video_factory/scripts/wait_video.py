import asyncio
from playwright.async_api import async_playwright

async def wait_for_video():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        flow_page = [x for x in context.pages if "flow.google.com" in x.url][0]
        await flow_page.bring_to_front()
        
        for attempt in range(30):
            await flow_page.wait_for_timeout(10000)
            
            # Find video tile
            tiles = await flow_page.locator("flow-grid-tile-container").all()
            print(f"[{attempt}] Total tiles: {len(tiles)}")
            tile_texts = [await t.inner_text() for t in tiles]
            print(f"Tiles: {tile_texts}")
            
            # Check if any tile has completed video tag
            videos = await flow_page.locator("video").all()
            print(f"Video tags found: {len(videos)}")
            if len(videos) > 0:
                print("🎉 Video tag detected!")
                for v in videos:
                    src = await v.get_attribute("src")
                    print(f"Video src: {src}")
                await flow_page.screenshot(path="/data/output/flow_video_completed.png")
                break
                
            # Check if percentage is gone or says completed
            if len(tile_texts) > 0 and not any("%" in t for t in tile_texts):
                print("🎉 Loading percentage is gone! Video likely completed.")
                await flow_page.screenshot(path="/data/output/flow_video_completed.png")
                break
                
        await flow_page.screenshot(path="/data/output/flow_progress_latest.png")

asyncio.run(wait_for_video())
