import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def download_video():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        flow_page = [x for x in context.pages if "flow.google.com" in x.url][0]
        await flow_page.bring_to_front()
        
        # Check menu options
        menu_items = await flow_page.locator("[role='menuitem']").all()
        print(f"Found {len(menu_items)} menu items:")
        for m in menu_items:
            print("  Item:", await m.inner_text())
            
        # Select 720p (or highest video quality)
        target_item = flow_page.locator("[role='menuitem']:has-text('720p'), [role='menuitem']:has-text('1080p')").first
        if await target_item.count() > 0:
            print(f"Clicking: {await target_item.inner_text()}")
            async with flow_page.expect_download(timeout=30000) as download_info:
                await target_item.click(force=True)
            download = await download_info.value
            target_path = Path("/data/output/generated_flow_video.mp4")
            await download.save_as(str(target_path))
            print(f"🎉 Download successful! Saved to {target_path} (size: {target_path.stat().st_size} bytes)")
        else:
            print("Target menu item not found")

asyncio.run(download_video())
