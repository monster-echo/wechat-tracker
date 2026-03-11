import asyncio
import base64
import json
import random
import logging
import os
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from mcp import ClientSession
from mcp.client.sse import sse_client
from motor.motor_asyncio import AsyncIOMotorCollection

from config import CollectorConfig

logger = logging.getLogger(__name__)

ArticleCallback = Callable[[str, Dict[str, Any]], Optional[Awaitable[None]]]


class WeChatCollector:
    def __init__(
        self,
        config: CollectorConfig,
        articles_collection: AsyncIOMotorCollection,
        accounts_collection: AsyncIOMotorCollection,
        on_new_article: Optional[ArticleCallback] = None,
    ):
        self.config = config
        self.articles_collection = articles_collection
        self.accounts_collection = accounts_collection
        self.on_new_article = on_new_article

    async def load_accounts(self) -> List[str]:
        cursor = self.accounts_collection.find({"enabled": {"$ne": False}})
        accounts = await cursor.to_list(length=1000)
        return [acc["name"] for acc in accounts if "name" in acc]


    async def check_login(self, session: ClientSession) -> bool:
        response = await session.call_tool("check_login_status", {})
        status = response.content[0].text
        return status == "LOGGED_IN"

    async def get_qrcode_and_wait(self, session: ClientSession) -> None:
        logger.info("检查登录状态，准备拉取二维码...")
        response = await session.call_tool("get_login_qrcode", {})
        content = response.content[0]

        if hasattr(content, "text"):
            qr_data = content.text
        elif hasattr(content, "data"):
            qr_data = content.data
        else:
            logger.warning("未知二维码响应类型：%s", type(content))
            return

        if qr_data == "ALREADY_LOGGED_IN":
            logger.info("检测到已登录。")
            return

        qr_bytes = base64.b64decode(qr_data)
        with open("qrcode.png", "wb") as file:
            file.write(qr_bytes)
        logger.info("二维码已保存到 qrcode.png，请微信扫码登录。")

        logger.info("等待扫码登录...")
        while True:
            await asyncio.sleep(5)
            if await self.check_login(session):
                logger.info("登录成功。")
                return

    async def search_articles(
        self,
        session: ClientSession,
        account_name: str,
    ) -> List[Dict[str, Any]]:
        response = await session.call_tool(
            "search_wechat_articles",
            {
                "account_name": account_name,
                "count": self.config.account_fetch_count,
            },
        )
        if not response.content:
            logger.warning("公众号 %s 响应内容为空", account_name)
            return []
            
        text_content = response.content[0].text
        articles: List[Dict[str, Any]] = []
        try:
            data = json.loads(text_content)
            if isinstance(data, dict) and "articles" in data:
                articles = data["articles"]
            elif isinstance(data, list):
                articles = data
        except json.JSONDecodeError:
            logger.warning("公众号 %s 返回非 JSON，响应内容：%s", account_name, text_content)
            wait_time = random.uniform(60, 300)
            logger.info("由于返回非 JSON 数据，随机等待 %.1f 秒...", wait_time)
            await asyncio.sleep(wait_time)
            return []

        # 增加 account 信息，并确保获取作者和发布时间（如果存在）
        for article in articles:
            if isinstance(article, dict):
                article["account"] = account_name
                # 这些字段通常由 MCP Tool 返回，如果不存在则设为 None 或保持原样
                if "author" not in article:
                    article["author"] = article.get("author_name") or "Unknown"
                if "publish_time" not in article:
                    # 尝试寻找可能的发布时间字段
                    article["publish_time"] = article.get("time") or article.get("pub_time")
                
        return articles

    async def _dispatch_new_article(self, account: str, article: Dict[str, Any]) -> None:
        if self.on_new_article is None:
            return
        result = self.on_new_article(account, article)
        if asyncio.iscoroutine(result):
            await result

    async def fetch_latest_articles(self) -> Dict[str, Any]:
        accounts = await self.load_accounts()
        if not accounts:
            logger.warning("账号列表为空，跳过采集。")
            return {"date": datetime.now().strftime("%Y-%m-%d"), "new_count": 0}

        today_str = datetime.now().strftime("%Y-%m-%d")
        fetch_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total_new_count = 0
        
        pending_accounts = list(accounts)
        max_retries = 5
        retry_count = 0

        while pending_accounts and retry_count < max_retries:
            try:
                logger.info("连接 MCP 服务：%s", self.config.mcp_server_url)
                async with sse_client(self.config.mcp_server_url) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        logger.info("MCP 会话初始化完成。")

                        logged_in = await self.check_login(session)
                        if not logged_in:
                            await self.get_qrcode_and_wait(session)

                        retry_count = 0

                        while pending_accounts:
                            account = pending_accounts[0]
                            index = len(accounts) - len(pending_accounts) + 1
                            logger.info("[%s/%s] 采集公众号：%s", index, len(accounts), account)
                            
                            articles = await self.search_articles(session, account)
                            await asyncio.sleep(random.uniform(2, 5))

                            account_new_count = 0
                            for article in articles:
                                if not isinstance(article, dict):
                                    continue
                                url = article.get("url", "")
                                if not url:
                                    continue
                                
                                # 检查是否已存在
                                existing = await self.articles_collection.find_one({"url": url})
                                if existing:
                                    continue

                                article["date_fetched"] = fetch_time_str
                                article["pdf_status"] = "pending"
                                
                                # 插入数据库
                                try:
                                    await self.articles_collection.insert_one(article)
                                    account_new_count += 1
                                    total_new_count += 1
                                    await self._dispatch_new_article(account, article)
                                except Exception as e:
                                    logger.error("文章入库失败: %s, error: %s", article.get("title"), e)

                            if account_new_count > 0:
                                logger.info("  -> 新增 %s 篇", account_new_count)
                            else:
                                logger.info("  -> 无新增")

                            pending_accounts.pop(0)

            except Exception as error:
                retry_count += 1
                logger.error("与 MCP 的通信中断或抛出异常：%s", error)
                if retry_count < max_retries:
                    logger.info("等待 10 秒后重新连接... (第 %s 次重试)", retry_count)
                    await asyncio.sleep(10)
                else:
                    logger.error("重试次数耗尽，放弃本次采集流程。")
                    break

        return {
            "date": today_str,
            "new_count": total_new_count,
            "accounts_count": len(accounts),
        }
