import asyncio
import json
import os
import re
from playwright.async_api import async_playwright

HISTORY_FILE = "articles_history.json"
OUTPUT_DIR = "pdf_exports"


def sanitize_filename(filename):
    """Remove invalid characters for filenames."""
    return re.sub(r'[\\/*?:"<>|]', "", filename).strip()


async def export_pdfs():
    if not os.path.exists(HISTORY_FILE):
        print(f"File {HISTORY_FILE} not found.")
        return

    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        try:
            history = json.load(f)
        except json.JSONDecodeError:
            print(f"Failed to parse {HISTORY_FILE}.")
            return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Starting Playwright...")
    # Using async context manager for playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        for account, articles in history.items():
            account_dir = os.path.join(OUTPUT_DIR, sanitize_filename(account))
            os.makedirs(account_dir, exist_ok=True)

            print(f"\nProcessing account: {account}")

            for article in articles:
                url = article.get("url")
                title = article.get("title", "Untitled")

                # Skip raw logs or empty URLs
                if not url:
                    continue

                safe_title = sanitize_filename(title)
                if not safe_title:
                    safe_title = "Untitled"

                pdf_path = os.path.join(account_dir, f"{safe_title}.pdf")

                if os.path.exists(pdf_path):
                    print(f"  Skipping (already exists): {title}")
                    continue

                print(f"  Exporting: {title} ...")
                try:
                    # Goto URL
                    await page.goto(url, wait_until="networkidle", timeout=30000)

                    # Scroll through the page progressively to trigger all lazy-loaded images
                    await page.evaluate(
                        """
                        async () => {
                            await new Promise((resolve) => {
                                let totalHeight = 0;
                                const distance = 200;
                                const timer = setInterval(() => {
                                    const scrollHeight = document.body.scrollHeight;
                                    window.scrollBy(0, distance);
                                    totalHeight += distance;

                                    if(totalHeight >= scrollHeight - window.innerHeight){
                                        clearInterval(timer);
                                        resolve();
                                    }
                                }, 100);
                            });
                        }
                    """
                    )

                    # Wait an extra 5-6 seconds for images/scripts to settle
                    await page.wait_for_timeout(6000)

                    # Generate PDF
                    await page.pdf(path=pdf_path, format="A4")
                    print(f"    -> Saved to {pdf_path}")
                except Exception as e:
                    print(f"    -> Error exporting: {e}")

        await browser.close()
        print("\nFinished exporting PDFs.")


if __name__ == "__main__":
    asyncio.run(export_pdfs())
