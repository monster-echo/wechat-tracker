import asyncio
import base64
import json
import os
import re
from datetime import datetime
from mcp import ClientSession
from mcp.client.sse import sse_client
from playwright.async_api import async_playwright
from apscheduler.schedulers.asyncio import AsyncIOScheduler

MCP_SERVER_URL = "http://127.0.0.1:8000/sse"
ACCOUNTS_FILE = "accounts.txt"
HISTORY_FILE = "articles_history.json"
DAILY_FOLDER = "daily_reports"
PDF_DIR = "pdf_exports"


def sanitize_filename(filename):
    """Remove invalid characters for filenames."""
    return re.sub(r'[\\/*?:"<>|]', "", str(filename)).strip()


async def check_login(session):
    try:
        response = await session.call_tool("check_login_status", {})
        status = response.content[0].text
        return status == "LOGGED_IN"
    except Exception as e:
        print(f"Error checking login status: {e}")
        return False


async def get_qrcode_and_wait(session):
    print("Not logged in. Fetching login QR code...")
    response = await session.call_tool("get_login_qrcode", {})

    content = response.content[0]
    if hasattr(content, "text"):
        qr_data = content.text
    elif hasattr(content, "data"):
        qr_data = content.data
    else:
        print(f"Unknown content type: {type(content)}")
        return

    if qr_data == "ALREADY_LOGGED_IN":
        print("Already logged in.")
        return

    try:
        qr_bytes = base64.b64decode(qr_data)
        with open("qrcode.png", "wb") as f:
            f.write(qr_bytes)
        print("Saved QR code to 'qrcode.png'. Please open it and scan with WeChat.")
    except Exception as e:
        print(f"Failed to save QR code image: {e}")
        return

    print("Waiting for login...", end="", flush=True)
    while True:
        await asyncio.sleep(5)
        if await check_login(session):
            print("\nSuccessfully logged in!")
            break
        print(".", end="", flush=True)


async def search_articles(session, account_name, count=10):
    try:
        response = await session.call_tool(
            "search_wechat_articles", {"account_name": account_name, "count": count}
        )
        text_content = response.content[0].text
        try:
            data = json.loads(text_content)
            if isinstance(data, dict) and "articles" in data:
                return data["articles"]
            if isinstance(data, list):
                return data
            return []
        except json.JSONDecodeError:
            return [{"title": "Raw Results", "url": "", "raw": text_content}]
    except Exception as e:
        print(f"Error searching articles for {account_name}: {e}")
        return []


def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        print(f"Please create '{ACCOUNTS_FILE}' with one account name per line.")
        return []

    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        accounts = [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]
    return accounts


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except:
                return {}
    return {}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


async def fetch_latest_articles():
    """Fetch new articles from MCP Server."""
    accounts = load_accounts()
    if not accounts:
        print("[Tracker] No accounts configured.")
        return

    history = load_history()
    today_str = datetime.now().strftime("%Y-%m-%d")
    daily_results = {}

    os.makedirs(DAILY_FOLDER, exist_ok=True)
    print(f"\n[Tracker] Connecting to WeChat MCP Server at {MCP_SERVER_URL} ...")

    try:
        async with sse_client(MCP_SERVER_URL) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()

                logged_in = await check_login(session)
                if not logged_in:
                    await get_qrcode_and_wait(session)

                print(f"[Tracker] Starting tracking for {len(accounts)} accounts...")

                for i, account in enumerate(accounts, 1):
                    print(f"[Tracker] [{i}/{len(accounts)}] Fetching '{account}'...")
                    # For continuous monitoring, we request a limited number of articles (e.g., 10)
                    articles = await search_articles(session, account, count=10)

                    if account not in history:
                        history[account] = []

                    known_urls = {
                        item.get("url", "")
                        for item in history[account]
                        if isinstance(item, dict) and item.get("url")
                    }

                    new_articles = []
                    for article in articles:
                        if isinstance(article, dict):
                            url = article.get("url", "")
                            if (url and url not in known_urls) or (
                                not url and article not in history[account]
                            ):
                                article["date_fetched"] = today_str
                                new_articles.append(article)
                                history[account].append(article)
                        elif isinstance(article, str):
                            new_articles.append(
                                {"raw": article, "date_fetched": today_str}
                            )
                            history[account].append(
                                {"raw": article, "date_fetched": today_str}
                            )

                    if new_articles:
                        print(f"  -> Found {len(new_articles)} new articles!")
                        daily_results[account] = new_articles
                    else:
                        print(f"  -> No new articles found.")

                    save_history(history)

                    if i < len(accounts):
                        print("  Waiting 10 seconds before next account...")
                        await asyncio.sleep(10)
    except Exception as e:
        print(f"[Tracker] Error during article fetch: {e}")

    # Save daily report
    if daily_results:
        report_path = os.path.join(DAILY_FOLDER, f"report_{today_str}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(daily_results, f, ensure_ascii=False, indent=2)
        print(f"[Tracker] Saved daily report to: {report_path}")


async def download_missing_pdfs():
    """Scan history and use Playwright to download missing PDFs."""
    history = load_history()
    tasks = []

    # Find everything missing a PDF
    for account, articles in history.items():
        account_dir = os.path.join(PDF_DIR, sanitize_filename(account))
        os.makedirs(account_dir, exist_ok=True)

        for article in articles:
            if isinstance(article, dict) and article.get("url"):
                title = article.get("title", "Untitled")
                safe_title = sanitize_filename(title) or "Untitled"

                already_exists = False
                if os.path.exists(account_dir):
                    existing_files = os.listdir(account_dir)
                    for f in existing_files:
                        if f == f"{safe_title}.pdf" or f.endswith(f"] {safe_title}.pdf"):
                            already_exists = True
                            break

                if not already_exists:
                    tasks.append((account, article, account_dir))

    if not tasks:
        print("[PDF] No missing PDFs to download.")
        return

    print(f"\n[PDF] Found {len(tasks)} missing articles. Starting Playwright export...")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            for account, article, account_dir in tasks:
                url = article.get("url")
                title = article.get("title", "Untitled")
                safe_title = sanitize_filename(title) or "Untitled"
                print(f"[PDF Worker] Loading: {title} ...")
                try:
                    await page.goto(url, wait_until="networkidle", timeout=30000)

                    publish_time_unix = await page.evaluate("window.ct")
                    if publish_time_unix:
                        try:
                            from datetime import datetime
                            date_str = datetime.fromtimestamp(int(publish_time_unix)).strftime("%Y-%m-%d")
                        except Exception:
                            date_str = article.get("date") or article.get("date_fetched") or "unknown_date"
                    else:
                        date_str = article.get("date") or article.get("date_fetched") or "unknown_date"

                    pdf_path = os.path.join(account_dir, f"[{date_str}] {safe_title}.pdf")
                    filename = os.path.basename(pdf_path)
                    print(f"[PDF Worker] Exporting: {filename} ...")

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

                    # Wait an extra 5~6 seconds for images/scripts to settle
                    await page.wait_for_timeout(6000)

                    await page.pdf(path=pdf_path, format="A4")
                    print(f"[PDF Worker]   -> Saved to {pdf_path}")
                except Exception as e:
                    print(f"[PDF Worker]   -> Error exporting: {e}")

            await browser.close()
    except Exception as e:
        print(f"[PDF] Playwright error: {e}")


async def scheduled_job():
    """The main routine that runs on the configured schedule."""
    print(
        f"\n{'='*60}\n>>> STARTING SCHEDULED JOB AT {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} <<<\n{'='*60}"
    )

    # 1. Fetch new articles
    await fetch_latest_articles()

    # 2. Download any new or historically missing PDFs
    await download_missing_pdfs()

    print(
        f"\n{'='*60}\n<<< FINISHED SCHEDULED JOB AT {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} >>>\n{'='*60}"
    )


async def main():
    print("Initializing APScheduler...")
    scheduler = AsyncIOScheduler()

    # Schedule the job to run every hour
    scheduler.add_job(scheduled_job, "interval", hours=1)
    scheduler.start()

    # Run the first job immediately
    await scheduled_job()

    print("\nScheduler is active. Press Ctrl+C to exit.")

    try:
        # Keep the main async loop running
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        print("Shutting down scheduler...")


if __name__ == "__main__":
    asyncio.run(main())
