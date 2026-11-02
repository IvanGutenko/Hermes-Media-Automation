import asyncio
from playwright.async_api import async_playwright

async def check_settings_dropdown():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        flow_page = [x for x in context.pages if "flow.google.com" in x.url][0]
        await flow_page.bring_to_front()
        
        # Open settings
        tune_btn = flow_page.locator("flow-base-prompt-box button[aria-label='Settings']")
        if await tune_btn.count() > 0:
            await tune_btn.first.click(force=True)
            await flow_page.wait_for_timeout(1000)
            
            # Find mat-select or select in settings panel
            selects = await flow_page.locator("mat-select, [role='combobox']").all()
            print(f"Found {len(selects)} select dropdowns")
            for i, s in enumerate(selects):
                print(f"Select {i} text: {await s.inner_text()}")
                
            # Click video model select (usually the 2nd one)
            if len(selects) >= 2:
                print("Clicking video model select...")
                await selects[1].click(force=True)
                await flow_page.wait_for_timeout(1000)
                
                # Check options in dropdown panel
                options = await flow_page.locator("mat-option, [role='option']").all()
                print(f"Options in dropdown: {len(options)}")
                for o in options:
                    print("  Option:", await o.inner_text())
                    if "Lite" in await o.inner_text():
                        print("Found Lite! Selecting it...")
                        await o.click(force=True)
                        await flow_page.wait_for_timeout(500)
                        
                        # Save
                        save_btn = flow_page.locator("button:has-text('Save')")
                        if await save_btn.count() > 0:
                            await save_btn.first.click(force=True)
                            print("Saved default settings with Veo 3.1 - Lite!")
                        break
            
            # Close settings
            close_btn = flow_page.locator("button[aria-label='Close'], button:has-text('close')")
            if await close_btn.count() > 0:
                await close_btn.first.click(force=True)

asyncio.run(check_settings_dropdown())
