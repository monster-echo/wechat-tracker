# WeChat Article Tracker

This Python project connects to your WeChat MCP server via SSE to fetch the latest articles for a list of official accounts daily.

## Setup

1. Make sure you have Python 3.10+ installed.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Edit `accounts.txt` with your list of 50 official accounts (one per line).

## Usage

Run the script daily to fetch new articles:
```bash
python wechat_tracker.py
```

- When you run the script, if you are not logged in, it will generate a `qrcode.png` file in this directory. Open it and scan it with your WeChat app to log in.
- The script will then iterate through the accounts, fetch the latest articles, and save them.
- New articles are added to `articles_history.json`.
- A daily snapshot is saved in the `daily_reports` folder.
- A small delay is introduced between fetching each account to prevent triggering WeChat's anti-crawler mechanisms.
