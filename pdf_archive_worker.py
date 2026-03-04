import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, Tuple

from playwright.async_api import async_playwright

from workflow_config import PdfConfig

logger = logging.getLogger(__name__)


def sanitize_filename(filename: str, max_length: int = 200) -> str:
    clean_name = re.sub(r'[\\/*?:"<>|]', "", str(filename)).strip()
    return clean_name[:max_length]


class PdfArchiveWorker:
    def __init__(self, config: PdfConfig):
        self.config = config
        self.queue: asyncio.Queue[Tuple[str, Dict[str, Any]]] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def enqueue(self, account: str, article: Dict[str, Any]) -> None:
        if not self.config.enabled:
            return
        await self.queue.put((account, article))

    def start(self) -> None:
        if not self.config.enabled:
            logger.info("PDF 导出功能已关闭。")
            return
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="pdf-archive-worker")
        logger.info("PDF Worker 已启动。")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("PDF Worker 已停止。")

    async def _run(self) -> None:
        while True:
            account, article = await self.queue.get()
            try:
                await self._export_pdf(account, article)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error("PDF 导出失败：%s", error, exc_info=True)
            finally:
                self.queue.task_done()

    async def _export_pdf(self, account: str, article: Dict[str, Any]) -> None:
        url = article.get("url", "")
        if not url:
            return

        ws_endpoint = self.config.playwright_ws_endpoint
        if ws_endpoint:
            if "?" in ws_endpoint:
                ws_endpoint += "&stealth&--disable-blink-features=AutomationControlled"
            else:
                ws_endpoint += "?stealth&--disable-blink-features=AutomationControlled"

        async with async_playwright() as playwright:
            if ws_endpoint:
                browser = await playwright.chromium.connect_over_cdp(ws_endpoint)
            else:
                browser = await playwright.chromium.launch(headless=True)

            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/16.6 Mobile/15E148 Safari/604.1"
                ),
                viewport={"width": 375, "height": 667},
                device_scale_factor=2,
                is_mobile=True,
                has_touch=True,
            )
            page = await context.new_page()
            try:
                title = article.get("title", "Untitled")
                safe_title = sanitize_filename(title) or "Untitled"
                account_dir = os.path.join(self.config.pdf_dir, sanitize_filename(account))
                os.makedirs(account_dir, exist_ok=True)

                logger.info("PDF 导出中：%s", title)
                await page.goto(url, wait_until="networkidle", timeout=30000)
                publish_time_unix = await page.evaluate("window.ct")
                if publish_time_unix:
                    try:
                        date_str = datetime.fromtimestamp(int(publish_time_unix)).strftime(
                            "%Y%m%d%H%M%S"
                        )
                    except Exception:
                        date_str = (
                            article.get("date")
                            or article.get("date_fetched")
                            or "unknown_date"
                        )
                else:
                    date_str = (
                        article.get("date") or article.get("date_fetched") or "unknown_date"
                    )

                pdf_path = os.path.join(account_dir, f"[{date_str}] {safe_title}.pdf")

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
                await page.wait_for_timeout(5000)
                await page.pdf(path=pdf_path, format="A4")
                logger.info("PDF 已保存：%s", pdf_path)
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
                try:
                    await context.close()
                    await browser.close()
                except Exception:
                    pass
