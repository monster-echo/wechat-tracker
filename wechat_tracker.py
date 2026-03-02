import asyncio
import base64
import json
import os
from datetime import datetime
from mcp import ClientSession
from mcp.client.sse import sse_client

MCP_SERVER_URL = "http://127.0.0.1:8000/sse"
ACCOUNTS_FILE = "accounts.txt"
HISTORY_FILE = "articles_history.json"
DAILY_FOLDER = "daily_reports"


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

    # Check content type
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
        print(
            f"Failed to save QR code image. The server might have returned non-base64 data: {qr_data[:50]}"
        )
        return

    print("Waiting for login...", end="", flush=True)
    while True:
        await asyncio.sleep(5)
        if await check_login(session):
            print("\nSuccessfully logged in!")
            break
        print(".", end="", flush=True)


async def search_articles(session, account_name):
    try:
        response = await session.call_tool(
            "search_wechat_articles", {"account_name": account_name}
        )
        text_content = response.content[0].text
        # The content might be a JSON string or formatted text.
        # Attempt to parse json
        try:
            data = json.loads(text_content)
            if isinstance(data, dict) and "articles" in data:
                return data["articles"]
            if isinstance(data, list):
                return data
            return []
        except json.JSONDecodeError:
            # If it's not JSON, let's return a dict with raw text
            return [{"title": "Raw Results", "url": "", "raw": text_content}]
    except Exception as e:
        print(f"Error searching articles for {account_name}: {e}")
        return []


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


def main_logic():
    # Load accounts
    if not os.path.exists(ACCOUNTS_FILE):
        print(f"Please create '{ACCOUNTS_FILE}' with one account name per line.")
        return

    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        accounts = [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]

    if not accounts:
        print(
            f"No accounts found in '{ACCOUNTS_FILE}'. Please add some accounts to track."
        )
        return

    accounts = accounts[:50]  # Enforce 50 max

    asyncio.run(run_tracker(accounts))


async def run_tracker(accounts):
    history = load_history()
    today_str = datetime.now().strftime("%Y-%m-%d")
    daily_results = {}

    os.makedirs(DAILY_FOLDER, exist_ok=True)

    print(f"Connecting to WeChat MCP Server at {MCP_SERVER_URL} ...")

    try:
        async with sse_client(MCP_SERVER_URL) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()

                logged_in = await check_login(session)
                if not logged_in:
                    await get_qrcode_and_wait(session)

                print(f"Starting tracking for {len(accounts)} accounts...")

                for i, account in enumerate(accounts, 1):
                    print(f"[{i}/{len(accounts)}] Fetching articles for '{account}'...")

                    # Fetch articles
                    articles = await search_articles(session, account)
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
                            # If url is missing or new, we treat as new
                            url = article.get("url", "")
                            if (url and url not in known_urls) or (
                                not url and article not in history[account]
                            ):
                                article["date_fetched"] = today_str
                                new_articles.append(article)
                                history[account].append(article)
                        elif isinstance(article, str):
                            # fallback for plain string list
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

                    # If there are more accounts, sleep to avoid rate limits (anti-crawler protection)
                    if i < len(accounts):
                        print("  Waiting 10 seconds before next account...")
                        await asyncio.sleep(10)

        # Save daily report
        if daily_results:
            report_path = os.path.join(DAILY_FOLDER, f"report_{today_str}.json")
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(daily_results, f, ensure_ascii=False, indent=2)
            print(f"\nSaved daily report with new articles to: {report_path}")
        else:
            print("\nNo new articles across all accounts today.")

    except Exception as e:
        print(f"\nAn error occurred while tracking: {e}")


if __name__ == "__main__":
    main_logic()
