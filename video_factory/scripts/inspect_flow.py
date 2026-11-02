import asyncio
from playwright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        flow_page = [x for x in context.pages if "flow.google.com" in x.url][0]
        await flow_page.bring_to_front()
        
        # Click Tools on left sidebar
        tools = flow_page.locator("text='Tools'").first
        await tools.click(force=True)
        await flow_page.wait_for_timeout(1500)
        await flow_page.screenshot(path="/data/output/flow_tools_clicked.png")
        print("Tools screenshot saved")
        
        # Inspect text in the tools menu/dialog
        body_text = await flow_page.locator("body").inner_text()
        print("Body text snippet with tools:")
        for line in body_text.split("\n"):
            if any(w in line.lower() for w in ["video", "veo", "scene", "animate", "image", "cinematic"]):
                print("  ", line)

asyncio.run(inspect())
