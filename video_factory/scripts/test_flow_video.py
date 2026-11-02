import asyncio
from playwright.async_api import async_playwright

async def test_video():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        flow_page = [x for x in context.pages if "flow.google.com" in x.url][0]
        await flow_page.bring_to_front()
        
        # Ensure backdrop closed
        bd = flow_page.locator(".cdk-overlay-backdrop")
        if await bd.count() > 0:
            await flow_page.keyboard.press("Escape")
            await flow_page.wait_for_timeout(500)
            
        # Check avatar in prompt box
        avatar_in_box = flow_page.locator("flow-base-prompt-box img")
        if await avatar_in_box.count() == 0:
            print("Attaching avatar...")
            add_btn = flow_page.locator("button[aria-label='Add ingredients to the prompt box']")
            if await add_btn.count() > 0:
                await add_btn.first.click(force=True)
                await flow_page.wait_for_timeout(1500)
                avatar_card = flow_page.locator("text=avatar.png, img[src*='avatar']").first
                if await avatar_card.count() > 0:
                    await avatar_card.click(force=True)
                    await flow_page.wait_for_timeout(1000)
                add_to_prompt = flow_page.locator("button:has-text('Add to prompt'):not([disabled])")
                if await add_to_prompt.count() > 0:
                    await add_to_prompt.first.click(force=True)
                    await flow_page.wait_for_timeout(1000)
        
        # Type explicit video prompt
        test_prompt = "Generate a video: A dynamic vertical 9:16 video of this developer character typing on his laptop in a glowing cyberpunk room. He raises his coffee mug and smiles at the camera."
        print(f"Typing prompt: {test_prompt}")
        input_div = flow_page.locator("div[aria-label='What do you want to create?']")
        await input_div.click(force=True)
        await input_div.fill(test_prompt)
        await flow_page.wait_for_timeout(500)
        
        # Click start generation
        start_btn = flow_page.locator("button[aria-label='Start generation']")
        print("Clicking Start generation...")
        await start_btn.click(force=True)
        await flow_page.wait_for_timeout(4000)
        
        await flow_page.screenshot(path="/data/output/flow_test_video_started.png")
        print("Screenshot saved: flow_test_video_started.png")
        
        # Inspect what cards are on screen
        tiles = await flow_page.locator("flow-grid-tile-container").all()
        print(f"Total tiles: {len(tiles)}")
        for i, t in enumerate(tiles):
            html = await t.inner_html()
            text = await t.inner_text()
            print(f"Tile {i} text: {text}")
            is_video = "flow-video-tile" in html or "video" in html
            print(f"Tile {i} is_video: {is_video}")

asyncio.run(test_video())
