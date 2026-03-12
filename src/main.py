import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from pdf_archive_worker import PdfArchiveWorker
from wechat_collector import WeChatCollector
from config import AppConfig, load_app_config
from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class WeChatTracker:
    def __init__(self, config: AppConfig):
        self.config = config
        self.scheduler = AsyncIOScheduler()
        
        # 初始化 MongoDB
        client = AsyncIOMotorClient(config.collector.mongodb_uri)
        db = client[config.collector.db_name]
        self.articles_collection = db["articles"]
        self.accounts_collection = db["accounts"]

        self.pdf_worker = PdfArchiveWorker(config.pdf, self.articles_collection)
        self.collector = WeChatCollector(
            config=config.collector,
            articles_collection=self.articles_collection,
            accounts_collection=self.accounts_collection,
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
        
        # 创建索引
        await self.articles_collection.create_index("url", unique=True)
        await self.accounts_collection.create_index("name", unique=True)

        # 尝试从 accounts.txt 迁移数据
        await self._migrate_accounts_if_needed()
        
        self.pdf_worker.start()
        
        # 启动时处理待处理的文章
        await self._enqueue_pending_articles()
        
        self.register_jobs()
        self.scheduler.start()
        
        asyncio.create_task(self.run_fetch_job())
        
        logger.info("系统运行中，按 Ctrl+C 停止。")
        while True:
            await asyncio.sleep(3600)

    async def _enqueue_pending_articles(self) -> None:
        logger.info("正在检查待处理的文章...")
        cursor = self.articles_collection.find({"pdf_status": "pending"})
        pending_count = 0
        async for article in cursor:
            account = article.get("account", "Unknown")
            await self.pdf_worker.enqueue(account, article)
            pending_count += 1
        
        if pending_count > 0:
            logger.info("已将 %d 篇待处理文章加入队列。", pending_count)
        else:
            logger.info("没有待处理的文章。")

    async def _migrate_accounts_if_needed(self) -> None:
        count = await self.accounts_collection.count_documents({})
        if count > 0:
            return

        # 查找旧的 accounts.txt
        data_dir = os.getenv("WECHAT_DATA_DIR", "data")
        old_file = os.path.join(data_dir, "accounts.txt")
        if not os.path.exists(old_file):
            return

        logger.info("发现旧的 accounts.txt，正在迁移到数据库...")
        try:
            with open(old_file, "r", encoding="utf-8") as f:
                accounts = [
                    line.strip()
                    for line in f
                    if line.strip() and not line.strip().startswith("#")
                ]
            
            for name in accounts:
                try:
                    await self.accounts_collection.update_one(
                        {"name": name},
                        {"$set": {"name": name, "enabled": True, "created_at": datetime.now()}},
                        upsert=True
                    )
                except Exception as e:
                    logger.error("迁移帐号失败 %s: %s", name, e)
            logger.info("帐号迁移完成共 %d 个。", len(accounts))
        except Exception as e:
            logger.error("读取旧帐号文件失败: %s", e)

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
