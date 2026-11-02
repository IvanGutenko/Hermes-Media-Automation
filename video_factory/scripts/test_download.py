import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def download_video():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        flow_page = [x for x in context.pages if "flow.google.com" in x.url][0]
        await flow_page.bring_to_front()
        
        dl_btn = flow_page.locator("button[aria-label='Download media']")
        if await dl_btn.count() > 0:
            print("Found Download media button! Triggering download...")
            async with flow_page.expect_download(timeout=30000) as download_info:
                await dl_btn.first.click(force=True)
            download = await download_info.value
            target_path = Path("/data/output/generated_flow_video.mp4")
            await download.save_as(str(target_path))
            print(f"🎉 Download successful! Saved to {target_path} (size: {target_path.stat().st_size} bytes)")
        else:
            print("Download button not found")

asyncio.run(download_video())
