import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def check():
    async with async_playwright() as p:
        user_data_dir = "/data/profile"
        print(f"Connecting to persistent context in {user_data_dir}...")
        try:
            # We connect to the headless or persistent context
            context = await p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            page = await context.new_page()
            await page.goto("https://myaccount.google.com", timeout=30000)
            url = page.url
            title = await page.title()
            print(f"Current URL: {url}")
            print(f"Page Title: {title}")
            
            if "signin" in url or "ServiceLogin" in url:
                print("STATUS: NOT_LOGGED_IN (Waiting for user login)")
            else:
                print("STATUS: AUTHENTICATED (Logged into Google successfully!)")
                
            await context.close()
        except Exception as e:
            print(f"Error checking status: {e}")

if __name__ == "__main__":
    asyncio.run(check())
