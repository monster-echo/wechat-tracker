import asyncio
import base64
import json
import logging
import os
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from mcp import ClientSession
from mcp.client.sse import sse_client

from workflow_config import CollectorConfig

logger = logging.getLogger(__name__)

ArticleCallback = Callable[[str, Dict[str, Any]], Optional[Awaitable[None]]]


class WeChatCollector:
    def __init__(
        self,
        config: CollectorConfig,
        on_new_article: Optional[ArticleCallback] = None,
    ):
        self.config = config
        self.on_new_article = on_new_article

    def load_accounts(self) -> List[str]:
        accounts_file = self.config.accounts_file
        if not os.path.exists(accounts_file):
            logger.warning("请创建账号文件：%s", accounts_file)
            return []

        with open(accounts_file, "r", encoding="utf-8") as file:
            return [
                line.strip()
                for line in file
                if line.strip() and not line.strip().startswith("#")
            ]

    def load_history(self) -> Dict[str, Any]:
        history_file = self.config.history_file
        if os.path.exists(history_file):
            with open(history_file, "r", encoding="utf-8") as file:
                try:
                    return json.load(file)
                except json.JSONDecodeError:
                    logger.warning("历史文件损坏，已回退为空：%s", history_file)
                    return {}
        return {}

    def save_history(self, history: Dict[str, Any]) -> None:
        history_file = self.config.history_file
        os.makedirs(os.path.dirname(history_file), exist_ok=True)
        with open(history_file, "w", encoding="utf-8") as file:
            json.dump(history, file, ensure_ascii=False, indent=2)

    async def check_login(self, session: ClientSession) -> bool:
        try:
            response = await session.call_tool("check_login_status", {})
            status = response.content[0].text
            return status == "LOGGED_IN"
        except Exception as error:
            logger.error("检查登录状态失败：%s", error)
            return False

    async def get_qrcode_and_wait(self, session: ClientSession) -> None:
        logger.info("未登录，准备拉取二维码...")
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

        try:
            qr_bytes = base64.b64decode(qr_data)
            with open("qrcode.png", "wb") as file:
                file.write(qr_bytes)
            logger.info("二维码已保存到 qrcode.png，请微信扫码登录。")
        except Exception as error:
            logger.error("二维码保存失败：%s", error)
            return

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
        try:
            response = await session.call_tool(
                "search_wechat_articles",
                {
                    "account_name": account_name,
                    "count": self.config.account_fetch_count,
                },
            )
            text_content = response.content[0].text
            data = json.loads(text_content)
            if isinstance(data, dict) and "articles" in data:
                return data["articles"]
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            logger.warning("公众号 %s 返回非 JSON，已忽略。", account_name)
        except Exception as error:
            logger.error("检索公众号失败 [%s]：%s", account_name, error)
        return []

    async def _dispatch_new_article(self, account: str, article: Dict[str, Any]) -> None:
        if self.on_new_article is None:
            return
        result = self.on_new_article(account, article)
        if asyncio.iscoroutine(result):
            await result

    async def fetch_latest_articles(self) -> Dict[str, Any]:
        accounts = self.load_accounts()
        if not accounts:
            logger.warning("账号列表为空，跳过采集。")
            return {"date": datetime.now().strftime("%Y-%m-%d"), "new_count": 0}

        history = self.load_history()
        today_str = datetime.now().strftime("%Y-%m-%d")
        daily_results: Dict[str, List[Dict[str, Any]]] = {}

        os.makedirs(self.config.daily_folder, exist_ok=True)
        logger.info("连接 MCP 服务：%s", self.config.mcp_server_url)

        try:
            async with sse_client(self.config.mcp_server_url) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    logger.info("MCP 会话初始化完成。")

                    logged_in = await self.check_login(session)
                    if not logged_in:
                        await self.get_qrcode_and_wait(session)

                    for index, account in enumerate(accounts, 1):
                        logger.info("[%s/%s] 采集公众号：%s", index, len(accounts), account)
                        articles = await self.search_articles(session, account)

                        if account not in history:
                            history[account] = []

                        known_urls = {
                            item.get("url", "")
                            for item in history[account]
                            if isinstance(item, dict) and item.get("url")
                        }
                        new_articles = []

                        for article in articles:
                            if not isinstance(article, dict):
                                continue
                            url = article.get("url", "")
                            if not url or url in known_urls:
                                continue
                            article["date_fetched"] = today_str
                            history[account].append(article)
                            new_articles.append(article)
                            await self._dispatch_new_article(account, article)

                        if new_articles:
                            daily_results[account] = new_articles
                            logger.info("  -> 新增 %s 篇", len(new_articles))
                        else:
                            logger.info("  -> 无新增")

                        self.save_history(history)
        except Exception as error:
            logger.error("采集流程异常：%s", error, exc_info=True)

        report_path = ""
        if daily_results:
            report_path = os.path.join(self.config.daily_folder, f"report_{today_str}.json")
            with open(report_path, "w", encoding="utf-8") as file:
                json.dump(daily_results, file, ensure_ascii=False, indent=2)
            logger.info("日报已保存：%s", report_path)

        new_count = sum(len(items) for items in daily_results.values())
        return {
            "date": today_str,
            "new_count": new_count,
            "report_path": report_path,
            "accounts_count": len(accounts),
        }
