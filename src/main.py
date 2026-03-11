import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from pdf_archive_worker import PdfArchiveWorker
from wechat_collector import WeChatCollector
from config import AppConfig, load_app_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class WeChatTracker:
    def __init__(self, config: AppConfig):
        self.config = config
        self.scheduler = AsyncIOScheduler()

        self.pdf_worker = PdfArchiveWorker(config.pdf)
        self.collector = WeChatCollector(
            config=config.collector,
            on_new_article=self._on_new_article,
        )

    async def _on_new_article(self, account: str, article: dict) -> None:
        await self.pdf_worker.enqueue(account, article)

    async def run_fetch_job(self) -> None:
        logger.info(">>> 开始爬取文章 <<<")
        fetch_res = await self.collector.fetch_latest_articles()
        logger.info("<<< 爬取文章结束 >>> new_count=%s", fetch_res.get("new_count", 0))

    def register_jobs(self) -> None:
        fetch_interval_hours = max(self.config.scheduler.fetch_interval_hours, 1)
        self.scheduler.add_job(
            self.run_fetch_job,
            "interval",
            hours=fetch_interval_hours,
            id="wechat_fetch_job",
            max_instances=1,
            coalesce=True,
        )
        logger.info("定时任务已注册：抓取每 %s 小时执行", fetch_interval_hours)

    async def start(self) -> None:
        logger.info("初始化调度器...")
        self.pdf_worker.start()
        self.register_jobs()
        self.scheduler.start()
        
        asyncio.create_task(self.run_fetch_job())
        
        logger.info("系统运行中，按 Ctrl+C 停止。")
        while True:
            await asyncio.sleep(3600)

    async def shutdown(self) -> None:
        logger.info("准备关闭调度器与后台任务...")
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        await self.pdf_worker.stop()


async def main() -> None:
    config = load_app_config()
    app = WeChatTracker(config)
    try:
        await app.start()
    except (KeyboardInterrupt, SystemExit):
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
