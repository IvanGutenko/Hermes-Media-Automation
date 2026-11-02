import asyncio
from playwright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        flow_page = [x for x in context.pages if "flow.google.com" in x.url][0]
        
        # Let's inspect buttons, mat-radio-button, or clickable elements in agent panel
        panel = flow_page.locator(".agent-panel-wrapper")
        items = await panel.locator("button, mat-radio-button, [role='radio'], [role='button'], div.option, .choice").all()
        print(f"Panel interactive items: {len(items)}")
        for i, it in enumerate(items):
            aria = await it.get_attribute("aria-label")
            text = await it.inner_text()
            tag = await it.evaluate("e => e.tagName")
            print(f"Item {i} [{tag}] aria='{aria}' text='{text.strip().replace(chr(10), ' ')}'")

asyncio.run(inspect())
