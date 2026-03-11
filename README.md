# WeChat Article Tracker

This Python project connects to your WeChat MCP server via SSE to continuously fetch the latest articles for a list of official accounts and automatically archives them as PDFs.

## Setup

1. Make sure you have Python 3.10+ installed.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   playwright install --with-deps chromium
   ```
3. Edit `data/accounts.txt` with your list of official accounts (one per line).
4. Configure your `.env` file if necessary (see configuration section below).

## Usage

Run the tracking service:
```bash
python -m src.main
```

- On the first run, if not logged in, the MCP server will generate a `qrcode.png` file in the current directory. Scan it with WeChat to log in.
- The service continuously runs in the background and collects new articles at set intervals.
- New articles are appended to `data/articles_history.json`.
- Daily snapshot reports are saved to `data/daily_reports`.
- PDF copies of the articles are automatically exported to `data/pdf_exports`.

## Configuration

You can customize the behavior by setting environment variables in a `.env` file or directly in your environment. The system will use sensible defaults if not set.

- `MCP_SERVER_URL`: The URL of your WeChat MCP server (default: `https://wechat-mcp.f.rwecho.top/sse`).
- `WECHAT_DATA_DIR`: Base directory for data storage (default: `data`).
- `FETCH_INTERVAL_HOURS`: Interval in hours between fetch cycles (default: `1`).
- `PDF_EXPORT_ENABLED`: Set to `false` or `off` to disable PDF archiving (default: `true`).
- `PLAYWRIGHT_WS_ENDPOINT`: Optional WebSocket endpoint for a remote Playwright browser instance, useful for stealth or remote execution.

## Scheduler Mechanism

The scheduler automatically executes two main tasks:
1. Hourly fetches of the WeChat official accounts data without manual CLI inputs.
2. Parallel PDF printing whenever a new article is discovered to archive a permanent copy.

For Docker usage, you can simply load the application which maps the necessary persistent volumes like `/app/pdf_exports` and `/app/daily_reports`.
