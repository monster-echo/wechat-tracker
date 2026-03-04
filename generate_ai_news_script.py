import argparse
import json
import logging
import os
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Tuple

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DATA_DIR = os.getenv("WECHAT_DATA_DIR", "/Volumes/workspace/docker-infras/wechat_data")
DAILY_FOLDER = os.path.join(DATA_DIR, "daily_reports")
HISTORY_FILE = os.path.join(DATA_DIR, "articles_history.json")
OUTPUT_DIR = os.path.join(DATA_DIR, "news_scripts")
DEFAULT_LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")
DEFAULT_LLM_MODEL = "deepseek-chat"
DEFAULT_LLM_API_KEY =  os.getenv("OPENAI_API_KEY")

AI_PATTERNS = [
    re.compile(r"(?<![a-z])ai(?![a-z])", re.IGNORECASE),
    re.compile(r"人工智能", re.IGNORECASE),
    re.compile(r"大模型", re.IGNORECASE),
    re.compile(r"生成式", re.IGNORECASE),
    re.compile(r"AIGC", re.IGNORECASE),
    re.compile(r"LLM", re.IGNORECASE),
    re.compile(r"ChatGPT", re.IGNORECASE),
    re.compile(r"OpenAI", re.IGNORECASE),
    re.compile(r"Claude", re.IGNORECASE),
    re.compile(r"Gemini", re.IGNORECASE),
    re.compile(r"DeepSeek", re.IGNORECASE),
    re.compile(r"Copilot", re.IGNORECASE),
    re.compile(r"MCP", re.IGNORECASE),
    re.compile(r"智能体", re.IGNORECASE),
    re.compile(r"Agent", re.IGNORECASE),
    re.compile(r"机器学习", re.IGNORECASE),
    re.compile(r"多模态", re.IGNORECASE),
    re.compile(r"算力", re.IGNORECASE),
    re.compile(r"推理", re.IGNORECASE),
    re.compile(r"训练", re.IGNORECASE),
    re.compile(r"机器人", re.IGNORECASE),
]

NEWS_PATTERNS = [
    re.compile(r"新闻|快讯|要闻|速览|早报|晚报|晨报|周报|日报|资讯"),
    re.compile(r"突发|发布|发布会|回应|通报|官宣|最新|现场|动态"),
    re.compile(r"财报|融资|政策|大会|峰会|会议|观察"),
]

NEWS_ACCOUNT_HINTS = [
    "新闻",
    "日报",
    "晚报",
    "人民网",
    "新华社",
    "央视",
    "36氪",
    "InfoQ",
    "AppSo",
    "机器之心",
    "量子位",
    "极客公园",
    "钛媒体",
    "财联社",
    "第一财经",
]

TREND_TERMS = [
    "OpenAI",
    "Claude",
    "Gemini",
    "DeepSeek",
    "ChatGPT",
    "智能体",
    "Agent",
    "机器人",
    "融资",
    "发布",
    "算力",
    "芯片",
    "编程",
    "教育",
    "医疗",
    "手机",
    "多模态",
    "MCP",
    "Copilot",
]

FOREIGN_MEDIA_ALLOWLIST = {
    "reuters",
    "bloomberg",
    "financial times",
    "ft",
    "the wall street journal",
    "wsj",
    "new york times",
    "nyt",
    "the verge",
    "techcrunch",
    "wired",
    "bbc",
    "cnbc",
    "the information",
    "mit technology review",
    "nature",
    "science",
    "associated press",
    "ap news",
    "forbes",
    "time",
}

DOMESTIC_SOURCE_BLOCKLIST = {
    "差评",
    "AppSo",
    "InfoQ",
    "36氪",
    "新智元",
    "机器之心",
    "量子位",
    "极客公园",
    "钛媒体",
    "虎嗅",
    "腾讯科技",
    "雷科技",
}

AI_STYLE_BANNED_PHRASES = [
    "首先",
    "其次",
    "再次",
    "最后",
    "总之",
    "综上所述",
    "接下来",
    "值得注意的是",
    "不可否认",
    "在当今时代",
    "随着",
    "本文将",
    "我们可以看到",
    "通过以上分析",
]

class NewsGenerator:
    def __init__(self, args) -> None:
        """Initialise NewsGenerator from a runtime-options object (argparse.Namespace
        or a compatible dataclass).  All properties are stored on self so that
        helper methods do not need to thread `args` through their parameters.
        """
        self.date: str = args.date
        self.source: str = args.source
        self.max_items: int = args.max_items
        self.topic_mode: str = args.topic_mode
        self.max_topics: int = args.max_topics
        self.max_candidates: int = args.max_candidates
        self.skip_drafts: bool = args.skip_drafts
        self.draft_model: str = getattr(args, "draft_model", "") or ""
        self.draft_dir: str = getattr(args, "draft_dir", "") or ""
        self.max_draft_topics: int = getattr(args, "max_draft_topics", 0) or 0
        self.min_draft_topics: int = getattr(args, "min_draft_topics", 0) or 0
        self.draft_target_words: int = getattr(args, "draft_target_words", 1600) or 1600
        self.min_imageholders: int = getattr(args, "min_imageholders", 3) or 3
        self.model: str = args.model
        self.api_base: str = args.api_base
        self.api_key: str = args.api_key
        self.no_fallback: bool = getattr(args, "no_fallback", False)
        self.output_md: str = getattr(args, "output_md", "") or ""
        self.output_txt: str = getattr(args, "output_txt", "") or ""

    def load_json_file(self, path: str):
        if not os.path.exists(path):
            return None

        with open(path, "r", encoding="utf-8") as file:
            try:
                return json.load(file)
            except json.JSONDecodeError:
                return None



    def safe_text(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()



    def sanitize_filename(self, filename: str, max_length: int = 80) -> str:
        clean_name = re.sub(r'[\\/*?:"<>|]', "", self.safe_text(filename))
        clean_name = re.sub(r"\s+", "_", clean_name)
        clean_name = clean_name.strip("._")
        if not clean_name:
            return "topic"
        return clean_name[:max_length]



    def normalize_article(self, account: str, item: Dict, target_date: str):
        if not isinstance(item, dict):
            return None

        title = self.safe_text(item.get("title", ""))
        if not title or title == "Raw Results":
            return None

        if item.get("raw") and title.lower().startswith("found "):
            return None

        return {
            "account": account.strip(),
            "title": title,
            "url": self.safe_text(item.get("url", "")),
            "date_fetched": self.safe_text(item.get("date_fetched", target_date)) or target_date,
        }



    def parse_daily_report(self, report_data: Dict, target_date: str) -> List[Dict]:
        records = []
        if not isinstance(report_data, dict):
            return records

        for account, items in report_data.items():
            if not isinstance(items, list):
                continue
            for item in items:
                normalized = self.normalize_article(account, item, target_date)
                if normalized:
                    records.append(normalized)
        return records



    def parse_history(self, history_data: Dict, target_date: str) -> List[Dict]:
        records = []
        if not isinstance(history_data, dict):
            return records

        for account, items in history_data.items():
            if not isinstance(items, list):
                continue
            for item in items:
                normalized = self.normalize_article(account, item, target_date)
                if not normalized:
                    continue
                if normalized["date_fetched"] == target_date:
                    records.append(normalized)
        return records



    def deduplicate_articles(self, records: List[Dict]) -> List[Dict]:
        seen = set()
        deduped = []
        for record in records:
            key = record["url"] or f'{record["account"]}::{record["title"]}'
            if key in seen:
                continue
            seen.add(key)
            deduped.append(record)
        return deduped



    def contains_any_pattern(self, text: str, patterns: List[re.Pattern]) -> bool:
        return any(pattern.search(text) for pattern in patterns)



    def hit_count(self, text: str, patterns: List[re.Pattern]) -> int:
        return sum(1 for pattern in patterns if pattern.search(text))



    def is_ai_related(self, article: Dict) -> bool:
        text = f'{article["title"]} {article["account"]}'
        return self.contains_any_pattern(text, AI_PATTERNS)



    def is_news_related(self, article: Dict) -> bool:
        title = article["title"]
        account = article["account"]
        if self.contains_any_pattern(title, NEWS_PATTERNS):
            return True
        return any(hint in account for hint in NEWS_ACCOUNT_HINTS)



    def rank_articles(self, records: List[Dict], patterns: List[re.Pattern]) -> List[Dict]:
        def sort_key(record: Dict):
            text = f'{record["title"]} {record["account"]}'
            return (-self.hit_count(text, patterns), record["account"], record["title"])

        return sorted(records, key=sort_key)



    def format_article_line(self, article: Dict) -> str:
        if article["url"]:
            return f'- [{article["title"]}]({article["url"]})（来源：{article["account"]}）'
        return f'- {article["title"]}（来源：{article["account"]}）'



    def top_trends(self, articles: List[Dict], top_n: int = 5) -> List[Tuple[str, int]]:
        counter = Counter()
        for article in articles:
            text = article["title"]
            for term in TREND_TERMS:
                if re.search(re.escape(term), text, re.IGNORECASE):
                    counter[term] += 1
        return counter.most_common(top_n)



    def format_cn_date(self, date_str: str) -> str:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return f"{dt.year}年{dt.month}月{dt.day}日"
        except ValueError:
            return date_str



    def build_keyword_broadcast_script(self, 
        date_str: str,
        total_count: int,
        news_count: int,
        ai_count: int,
        ai_news: List[Dict],
        ai_only: List[Dict],
        news_only: List[Dict],
        max_items: int,
    ) -> str:
        lines = []
        cn_date = self.format_cn_date(date_str)
        lines.append(f"大家好，今天是{cn_date}。下面为你带来今天微信公众号里的新闻与AI简报。")
        lines.append(
            f"今天共筛选到 {total_count} 条候选内容，其中新闻向 {news_count} 条，AI相关 {ai_count} 条，新闻与AI交叉 {len(ai_news)} 条。"
        )

        if ai_news:
            lines.append("先看最值得关注的 AI 新闻交叉内容：")
            for index, article in enumerate(ai_news[:max_items], 1):
                lines.append(f"{index}、{article['title']}。来源：{article['account']}。")

        if ai_only:
            lines.append("再看 AI 专题方向：")
            for index, article in enumerate(ai_only[:max_items], 1):
                lines.append(f"{index}、{article['title']}。来源：{article['account']}。")

        if news_only:
            lines.append("最后补充几条非 AI 但值得关注的新闻：")
            for index, article in enumerate(news_only[:max_items], 1):
                lines.append(f"{index}、{article['title']}。来源：{article['account']}。")

        trends = self.top_trends(ai_news + ai_only)
        if trends:
            trend_text = "、".join(f"{term}（{count}）" for term, count in trends)
            lines.append(f"从关键词热度看，今天 AI 讨论集中在：{trend_text}。")

        lines.append("以上就是今天的AI新闻速览，我们明天继续追踪。")
        return "\n".join(lines)



    def build_keyword_markdown_report(self, 
        date_str: str,
        source_name: str,
        records: List[Dict],
        ai_news: List[Dict],
        ai_only: List[Dict],
        news_only: List[Dict],
        max_items: int,
        broadcast_script: str,
    ) -> str:
        all_ai = ai_news + ai_only
        all_news = ai_news + news_only

        markdown_lines = [
            f"# {date_str} 微信公众号「新闻 + AI」提取稿",
            "",
            "## 数据说明",
            f"- 目标日期：`{date_str}`",
            f"- 读取来源：`{source_name}`",
            f"- 扫描文章数：`{len(records)}`",
            f"- 新闻向条数：`{len(all_news)}`",
            f"- AI相关条数：`{len(all_ai)}`",
            f"- 新闻与AI交叉：`{len(ai_news)}`",
            "",
            "## AI 新闻交叉（优先关注）",
        ]

        if ai_news:
            markdown_lines.extend(self.format_article_line(item) for item in ai_news[:max_items])
        else:
            markdown_lines.append("- 今日未命中 AI + 新闻交叉内容。")

        markdown_lines.extend(["", "## AI 相关（含评论/测评/工具）"])
        if ai_only:
            markdown_lines.extend(self.format_article_line(item) for item in ai_only[:max_items])
        else:
            markdown_lines.append("- 今日未命中额外 AI 内容。")

        markdown_lines.extend(["", "## 新闻相关（非AI）"])
        if news_only:
            markdown_lines.extend(self.format_article_line(item) for item in news_only[:max_items])
        else:
            markdown_lines.append("- 今日未命中额外新闻内容。")

        trend_items = self.top_trends(all_ai)
        markdown_lines.extend(["", "## AI 热词", ""])
        if trend_items:
            for term, count in trend_items:
                markdown_lines.append(f"- {term}：{count}")
        else:
            markdown_lines.append("- 今日暂无明显 AI 热词。")

        markdown_lines.extend(["", "## 可直接播报文字稿", "", broadcast_script, ""])
        return "\n".join(markdown_lines)



    def parse_llm_json(self, text: str) -> Dict[str, Any]:
        content = self.safe_text(text)
        if not content:
            raise ValueError("模型返回内容为空。")

        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        matched = re.search(r"\{[\s\S]*\}", content)
        if not matched:
            raise ValueError("模型返回中未找到 JSON 对象。")

        try:
            parsed = json.loads(matched.group(0))
        except json.JSONDecodeError as error:
            raise ValueError(f"模型返回 JSON 解析失败：{error}") from error

        if not isinstance(parsed, dict):
            raise ValueError("模型返回 JSON 结构不是对象。")
        return parsed



    def normalize_indexes(self, raw_indexes: Any, max_index: int) -> List[int]:
        if not isinstance(raw_indexes, list):
            return []

        cleaned = []
        seen = set()
        for item in raw_indexes:
            if isinstance(item, bool):
                continue
            if isinstance(item, int):
                index = item
            elif isinstance(item, str) and item.strip().isdigit():
                index = int(item.strip())
            else:
                continue

            if 1 <= index <= max_index and index not in seen:
                seen.add(index)
                cleaned.append(index)
        return cleaned



    def build_candidate_text(self, records: List[Dict], max_candidates: int) -> Tuple[List[Dict], str]:
        candidates = records[:max_candidates]
        lines = []

        for index, article in enumerate(candidates, 1):
            url = article["url"] or "N/A"
            lines.append(
                f"[{index}] 标题：{article['title']} | 来源：{article['account']} | 链接：{url}"
            )
        return candidates, "\n".join(lines)



    def call_chat_completion(self, 
        api_base: str, api_key: str, model: str, messages: List[Dict[str, str]]
    ) -> str:
        if not api_key:
            raise ValueError("缺少 API Key，请设置 OPENAI_API_KEY 或传入 --api-key。")

        base_url = self.safe_text(api_base).rstrip("/")
        if not base_url:
            raise ValueError("API Base URL 为空，请传入 --api-base。")

        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
        }

        with httpx.Client(timeout=120) as client:
            response = client.post(url, headers=headers, json=payload)

        if response.status_code >= 400:
            body_preview = self.safe_text(response.text)[:300]
            raise RuntimeError(
                f"模型请求失败（HTTP {response.status_code}）：{body_preview}"
            )

        data = response.json()
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("模型返回不包含 choices。")

        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict):
                    part_text = self.safe_text(part.get("text", ""))
                    if part_text:
                        text_parts.append(part_text)
            merged = "".join(text_parts)
            if merged:
                return merged

        raise RuntimeError("模型返回内容格式无法解析。")



    def choose_topics_with_ai(self, records: List[Dict], args, date_str: str) -> Dict[str, Any]:
        candidates, candidates_text = self.build_candidate_text(records, self.max_candidates)
        if not candidates:
            raise ValueError("没有可供 AI 选题的候选文章。")

        system_prompt = (
            "你是资深中文科技媒体主编。"
            "你的任务是从候选微信公众号文章中筛选最值得做“AI相关选题”的主题。"
            "必须忠于候选列表，不得编造事实。"
        )

        user_prompt = f"""
    日期：{date_str}
    请从下面候选文章中挑选 3~{max(3, self.max_topics)} 个“AI相关选题”。
    选题标准：优先关注 AI 产品发布、模型能力升级、产业影响、政策与伦理、商业化落地、AI 对各行业影响。
    你可以忽略与 AI 无关的文章。

    候选文章列表：
    {candidates_text}

    请严格返回 JSON（不要 markdown，不要代码块），结构如下：
    {{
      "opening": "开场白，一句话",
      "topics": [
        {{
          "topic": "选题标题",
          "reason": "为什么值得讲（20~60字）",
          "narrative_angle": "怎么讲这个选题（20~60字）",
          "article_indexes": [1, 7, 10]
        }}
      ],
      "selected_article_indexes": [1, 7, 10, 12],
      "closing": "结尾一句话"
    }}

    约束：
    1) article_indexes 与 selected_article_indexes 只能用候选文章编号；
    2) topics 数量不要超过 {self.max_topics}；
    3) 若当天几乎没有 AI 相关内容，可返回空 topics 和空 selected_article_indexes，并在 opening 里说明。
    """.strip()

        content = self.call_chat_completion(
            api_base=self.api_base,
            api_key=self.api_key,
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        parsed = self.parse_llm_json(content)
        max_index = len(candidates)
        selected_indexes = set(
            self.normalize_indexes(parsed.get("selected_article_indexes", []), max_index)
        )

        topics = []
        raw_topics = parsed.get("topics", [])
        if isinstance(raw_topics, list):
            for raw_topic in raw_topics[: self.max_topics]:
                if not isinstance(raw_topic, dict):
                    continue

                topic_name = self.safe_text(raw_topic.get("topic"))
                if not topic_name:
                    continue

                topic_indexes = self.normalize_indexes(raw_topic.get("article_indexes", []), max_index)
                if not topic_indexes:
                    continue

                for idx in topic_indexes:
                    selected_indexes.add(idx)

                related_articles = [candidates[idx - 1] for idx in topic_indexes]
                topics.append(
                    {
                        "topic": topic_name,
                        "reason": self.safe_text(raw_topic.get("reason")),
                        "narrative_angle": self.safe_text(raw_topic.get("narrative_angle")),
                        "article_indexes": topic_indexes,
                        "articles": related_articles,
                    }
                )

        if topics:
            topic_indexes = set()
            for topic in topics:
                topic_indexes.update(topic["article_indexes"])
            selected_indexes = topic_indexes

        selected_articles = [candidates[idx - 1] for idx in sorted(selected_indexes)]
        return {
            "opening": self.safe_text(parsed.get("opening")),
            "closing": self.safe_text(parsed.get("closing")),
            "topics": topics,
            "selected_articles": selected_articles,
            "candidate_count": len(candidates),
            "truncated": len(records) > len(candidates),
            "model": self.model,
        }



    def build_ai_broadcast_script(self, date_str: str, ai_plan: Dict[str, Any], max_items: int) -> str:
        lines = []
        opening = ai_plan.get("opening") or (
            f"大家好，今天是{self.format_cn_date(date_str)}。下面由 AI 为你挑选今天最值得关注的科技选题。"
        )
        lines.append(opening)
        lines.append(
            f"AI 在 {ai_plan.get('candidate_count', 0)} 条候选里，筛出了 {len(ai_plan.get('topics', []))} 个重点选题。"
        )

        topics = ai_plan.get("topics", [])
        if topics:
            for index, topic in enumerate(topics[:max_items], 1):
                reason = self.safe_text(topic.get("reason"))
                angle = self.safe_text(topic.get("narrative_angle"))
                lines.append(f"{index}、{topic['topic']}。")
                if reason:
                    lines.append(f"选题理由：{reason}")
                if angle:
                    lines.append(f"讲述角度：{angle}")

                article_titles = []
                for article in topic.get("articles", [])[:3]:
                    article_titles.append(f"{article['title']}（{article['account']}）")
                if article_titles:
                    lines.append("关联文章：" + "；".join(article_titles) + "。")
        else:
            lines.append("今天未筛出高置信度 AI 选题，建议继续采集更多科技类公众号内容后再生成。")

        closing = ai_plan.get("closing") or "以上就是 AI 为你挑选的今日重点选题。"
        lines.append(closing)
        return "\n".join(lines)



    def build_ai_markdown_report(self, 
        date_str: str,
        source_name: str,
        records: List[Dict],
        ai_plan: Dict[str, Any],
        max_items: int,
        draft_files: List[str] = None,
    ) -> str:
        topics = ai_plan.get("topics", [])
        selected_articles = ai_plan.get("selected_articles", [])
        markdown_lines = [
            f"# {date_str} 微信公众号「AI选题」提取稿",
            "",
            "## 数据说明",
            f"- 目标日期：`{date_str}`",
            f"- 读取来源：`{source_name}`",
            f"- 扫描文章数：`{len(records)}`",
            f"- AI候选输入：`{ai_plan.get('candidate_count', 0)}`",
            f"- AI模型：`{ai_plan.get('model', 'N/A')}`",
            f"- 最终选题数：`{len(topics)}`",
            f"- 入选文章数：`{len(selected_articles)}`",
        ]

        if ai_plan.get("truncated"):
            markdown_lines.append("- 注：候选文章过多，已按 `--max-candidates` 截断后再交给模型。")

        markdown_lines.extend(["", "## AI 选题结果"])
        if topics:
            for index, topic in enumerate(topics[:max_items], 1):
                markdown_lines.append(f"### 选题 {index}：{topic['topic']}")

                if topic.get("reason"):
                    markdown_lines.append(f"- 选题理由：{topic['reason']}")
                if topic.get("narrative_angle"):
                    markdown_lines.append(f"- 讲述角度：{topic['narrative_angle']}")

                markdown_lines.append("- 关联文章：")
                for article in topic.get("articles", [])[:max_items]:
                    markdown_lines.append(self.format_article_line(article))
                markdown_lines.append("")
        else:
            markdown_lines.append("- 今日未筛出高置信度 AI 选题。")

        markdown_lines.extend(["## 入选文章清单"])
        if selected_articles:
            for article in selected_articles[: max_items * 3]:
                markdown_lines.append(self.format_article_line(article))
        else:
            markdown_lines.append("- 今日暂无入选文章。")

        if draft_files:
            markdown_lines.extend(["", "## 已生成选题成稿"])
            for path in draft_files:
                markdown_lines.append(f"- `{path}`")

        broadcast_script = self.build_ai_broadcast_script(date_str, ai_plan, max_items)
        markdown_lines.extend(["", "## 可直接播报文字稿", "", broadcast_script, ""])
        return "\n".join(markdown_lines)



    def build_topic_sources_text(self, topic: Dict[str, Any]) -> str:
        lines = []
        for index, article in enumerate(topic.get("articles", []), 1):
            url = article.get("url") or "N/A"
            lines.append(
                f"{index}. 标题：{article.get('title', '')} | 来源：{article.get('account', '')} | 链接：{url}"
            )
        return "\n".join(lines)



    def is_foreign_media_name(self, source_name: str) -> bool:
        normalized = self.safe_text(source_name).lower()
        if not normalized:
            return False
        return any(token in normalized for token in FOREIGN_MEDIA_ALLOWLIST)



    def collect_blocked_wechat_sources(self, topic: Dict[str, Any]) -> List[str]:
        blocked = []
        seen = set()

        for source_name in DOMESTIC_SOURCE_BLOCKLIST:
            if source_name in seen:
                continue
            seen.add(source_name)
            blocked.append(source_name)

        for article in topic.get("articles", []):
            account_name = self.safe_text(article.get("account"))
            if not account_name:
                continue
            if self.is_foreign_media_name(account_name):
                continue
            if account_name in seen:
                continue
            seen.add(account_name)
            blocked.append(account_name)
        return blocked



    def mask_blocked_sources(self, markdown_text: str, blocked_sources: List[str]) -> str:
        if not blocked_sources:
            return markdown_text

        sanitized = markdown_text
        for source_name in sorted(blocked_sources, key=len, reverse=True):
            escaped_name = re.escape(source_name)
            sanitized = re.sub(escaped_name, "某科技媒体", sanitized)
        return sanitized



    def find_banned_style_phrases(self, markdown_text: str) -> List[str]:
        hits = []
        for phrase in AI_STYLE_BANNED_PHRASES:
            if re.search(re.escape(phrase), markdown_text):
                hits.append(phrase)
        return hits



    def polish_article_style(self, 
        markdown_text: str,
        topic_name: str,
        args,
        draft_model: str,
        blocked_sources: List[str],
    ) -> str:
        blocked_sources_text = "、".join(blocked_sources) if blocked_sources else "（无）"
        banned_text = "、".join(AI_STYLE_BANNED_PHRASES)

        system_prompt = (
            "你是中文科技媒体资深编辑，请把稿子改成更像真人写作的科技长文。"
            "文风参考‘差评式’节奏：有画面感、有观点、有转折，但信息必须克制准确。"
        )

        user_prompt = f"""
    请在不改动事实前提下，润色下面这篇稿子，使其更自然、更像人写的科技媒体文章。

    硬性要求：
    1) 去掉模板化AI表达，禁止出现：{banned_text}
    2) 禁止出现公众号来源名：{blocked_sources_text}
    3) 可以保留国外媒体名称（Reuters/Bloomberg/WSJ/The Verge/TechCrunch等）
    4) 必须完整保留并原样保留所有 [IMAGEHOLDER] ... [/IMAGEHOLDER] 占位块
    5) 不新增不存在的事实，不删掉核心信息
    6) 只输出润色后的 Markdown 正文，不要解释

    原稿如下：
    {markdown_text}
    """.strip()

        return self.call_chat_completion(
            api_base=self.api_base,
            api_key=self.api_key,
            model=draft_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )



    def count_imageholders(self, markdown_text: str) -> int:
        return len(re.findall(r"\[IMAGEHOLDER\]", markdown_text))



    def build_imageholder_block(self, topic_name: str, sequence: int) -> str:
        return (
            "\n[IMAGEHOLDER]\n"
            f"- 搜索关键词：{topic_name}, 科技新闻, 人工智能, 产业应用\n"
            f"- 图片描述：与“{topic_name}”相关的新闻现场或产品概念视觉，第 {sequence} 张配图\n"
            "- 建议位置：正文段落之间，用于承接上下文转场\n"
            "[/IMAGEHOLDER]\n"
        )



    def ensure_min_imageholders(self, markdown_text: str, topic_name: str, min_count: int) -> str:
        if min_count <= 0:
            return markdown_text

        current_count = self.count_imageholders(markdown_text)
        if current_count >= min_count:
            return markdown_text

        missing = min_count - current_count
        extra_blocks = []
        for sequence in range(current_count + 1, current_count + missing + 1):
            extra_blocks.append(self.build_imageholder_block(topic_name, sequence))

        logger.warning(
            "选题《%s》IMAGEHOLDER 不足（当前 %s，要求 %s），已自动补齐。",
            topic_name,
            current_count,
            min_count,
        )
        return markdown_text.rstrip() + "\n\n" + "\n".join(extra_blocks).strip() + "\n"



    def generate_topic_article_markdown(self, 
        date_str: str,
        topic: Dict[str, Any],
        args,
        draft_model: str,
    ) -> str:
        topic_name = self.safe_text(topic.get("topic")) or "未命名选题"
        topic_reason = self.safe_text(topic.get("reason")) or "该选题具有较强的行业讨论价值。"
        narrative_angle = self.safe_text(topic.get("narrative_angle")) or "从行业影响与落地场景切入。"
        source_text = self.build_topic_sources_text(topic)
        blocked_sources = self.collect_blocked_wechat_sources(topic)
        blocked_sources_text = "、".join(blocked_sources) if blocked_sources else "（无）"

        system_prompt = (
            "你是中国一线科技媒体主笔，文风参考差评：开头抓人、节奏紧凑、观点鲜明但不过火。"
            "请输出自然、有人味、带现场感的媒体稿，不要出现“作为AI”“我认为”这类AI口吻。"
            "不能编造采访、数据、时间、人物观点；没有事实依据时用趋势性、背景性表达。"
        )

        user_prompt = f"""
    请基于以下选题信息，写一篇可直接发布的中文图文稿（Markdown）：

    日期：{date_str}
    选题：{topic_name}
    选题理由：{topic_reason}
    叙事角度：{narrative_angle}
    参考素材（仅限下列）：
    {source_text if source_text else "暂无具体链接，请以选题背景延展写作。"}

    写作要求：
    1) 风格（重点）：参考“差评式”叙事节奏，先抛冲突/反差，再给事实与判断；语言像真人编辑，不像报告；
    2) 字数：约 {max(self.draft_target_words, 600)} 字；
    3) 结构：标题 + 导语 + 3~5 个小节 + 结尾“你怎么看”互动句；
    4) 图文结合：至少 {max(self.min_imageholders, 1)} 处 IMAGEHOLDER，占位格式必须完全一致：

    [IMAGEHOLDER]
    - 搜索关键词：关键词1, 关键词2, 关键词3
    - 图片描述：给后续制图/搜图同学的具体描述（15~40字）
    - 建议位置：放在第几节后面，或导语后
    [/IMAGEHOLDER]

    5) 只输出 Markdown 正文，不要解释，不要代码块。
    6) 来源引用规则（必须严格遵守）：
       - 以下公众号来源名称禁止在正文出现：{blocked_sources_text}
       - 禁止出现“据XX公众号/来自XX公众号/XX公众号称”这类表述；
       - 如果信息来自国外媒体，可正常保留媒体名（如 Reuters、Bloomberg、WSJ、The Verge、TechCrunch 等）；
       - 当无法确认可公开外媒来源时，用“有行业观点认为/市场消息显示”等中性表达替代。
    7) 去AI味（必须遵守）：
       - 禁止出现这些词：{("、".join(AI_STYLE_BANNED_PHRASES))}
       - 禁止“第一点/第二点”报告体、禁止“一方面/另一方面”模板句；
       - 段落尽量短，少用空洞总结句，避免官腔和教学腔。
    """.strip()

        markdown = self.call_chat_completion(
            api_base=self.api_base,
            api_key=self.api_key,
            model=draft_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        markdown = self.mask_blocked_sources(markdown, blocked_sources)

        style_hits = self.find_banned_style_phrases(markdown)
        if style_hits:
            logger.info(
                "选题《%s》检测到模板化表达（%s），执行风格润色。",
                topic_name,
                "、".join(style_hits[:6]),
            )
            try:
                polished = self.polish_article_style(
                    markdown_text=markdown,
                    topic_name=topic_name,
                    args=args,
                    draft_model=draft_model,
                    blocked_sources=blocked_sources,
                )
                markdown = self.mask_blocked_sources(polished, blocked_sources)
            except Exception as error:
                logger.warning("选题《%s》风格润色失败，保留初稿：%s", topic_name, error)

        return self.ensure_min_imageholders(markdown, topic_name, self.min_imageholders)



    def generate_topic_drafts(self, 
        date_str: str,
        ai_plan: Dict[str, Any],
        args,
    ) -> List[str]:
        topics = ai_plan.get("topics", [])
        if not topics:
            logger.info("没有可生成成稿的选题，跳过成稿步骤。")
            return []

        max_topics = max(
            int(getattr(self, "max_draft_topics", 0) or 0),
            int(getattr(self, "min_draft_topics", 0) or 0),
            0,
        )
        draft_topics = topics[:max_topics]
        if not draft_topics:
            logger.info("max_draft_topics=0，跳过成稿步骤。")
            return []

        draft_dir = self.draft_dir or os.path.join(OUTPUT_DIR, f"topic_drafts_{date_str}")
        os.makedirs(draft_dir, exist_ok=True)
        draft_model = self.draft_model or self.model

        generated_files = []
        total_count = len(draft_topics)
        for index, topic in enumerate(draft_topics, 1):
            topic_name = self.safe_text(topic.get("topic")) or f"选题{index}"
            file_name = f"{index:02d}_{self.sanitize_filename(topic_name)}.md"
            file_path = os.path.join(draft_dir, file_name)

            logger.info("正在生成选题成稿 [%s/%s]：%s", index, total_count, topic_name)
            article_markdown = self.generate_topic_article_markdown(
                date_str=date_str,
                topic=topic,
                args=args,
                draft_model=draft_model,
            )

            with open(file_path, "w", encoding="utf-8") as file:
                file.write(article_markdown.rstrip() + "\n")

            generated_files.append(file_path)
            logger.info("已生成选题成稿：%s", file_path)

        return generated_files



    def collect_articles(self, target_date: str, source: str) -> Tuple[List[Dict], str]:
        report_path = os.path.join(DAILY_FOLDER, f"report_{target_date}.json")
        source_names = []
        records: List[Dict] = []

        if source in ("auto", "report"):
            report_data = self.load_json_file(report_path)
            if report_data is not None:
                records.extend(self.parse_daily_report(report_data, target_date))
                source_names.append(report_path)
            elif source == "report":
                return [], report_path

        if source in ("auto", "history"):
            history_data = self.load_json_file(HISTORY_FILE)
            if history_data is not None:
                records.extend(self.parse_history(history_data, target_date))
                source_names.append(HISTORY_FILE)
            elif source == "history":
                return [], HISTORY_FILE

        deduped = self.deduplicate_articles(records)
        source_name = " + ".join(source_names) if source_names else "N/A"
        return deduped, source_name



    def build_keyword_selection(self, records: List[Dict]):
        ai_related = [item for item in records if self.is_ai_related(item)]
        news_related = [item for item in records if self.is_news_related(item)]

        ai_news = [item for item in ai_related if self.is_news_related(item)]
        ai_only = [item for item in ai_related if item not in ai_news]
        news_only = [item for item in news_related if item not in ai_news]

        ai_news = self.rank_articles(ai_news, AI_PATTERNS + NEWS_PATTERNS)
        ai_only = self.rank_articles(ai_only, AI_PATTERNS)
        news_only = self.rank_articles(news_only, NEWS_PATTERNS)
        return ai_related, news_related, ai_news, ai_only, news_only



    def ensure_minimum_ai_topics(self, ai_plan: Dict[str, Any], records: List[Dict], min_topics: int) -> Dict[str, Any]:
        min_topics = max(int(min_topics or 0), 0)
        if min_topics <= 0:
            return ai_plan

        original_topics = ai_plan.get("topics", [])
        topics = list(original_topics) if isinstance(original_topics, list) else []
        if len(topics) >= min_topics:
            return ai_plan

        used_article_keys = set()
        for topic in topics:
            for article in topic.get("articles", []):
                key = article.get("url") or f'{article.get("account", "")}::{article.get("title", "")}'
                if key:
                    used_article_keys.add(key)

        ranked_candidates = self.rank_articles(records, AI_PATTERNS + NEWS_PATTERNS)
        for article in ranked_candidates:
            if len(topics) >= min_topics:
                break

            key = article.get("url") or f'{article.get("account", "")}::{article.get("title", "")}'
            if key in used_article_keys:
                continue
            used_article_keys.add(key)

            topic_title = self.safe_text(article.get("title")) or "补位选题"
            topics.append(
                {
                    "topic": topic_title,
                    "reason": "当日新增热点信息，具备延展成文价值。",
                    "narrative_angle": "围绕事件进展、产业影响与可落地场景展开。",
                    "article_indexes": [],
                    "articles": [article],
                    "auto_filled": True,
                }
            )

        selected_articles = []
        selected_seen = set()
        for topic in topics:
            for article in topic.get("articles", []):
                key = article.get("url") or f'{article.get("account", "")}::{article.get("title", "")}'
                if key in selected_seen:
                    continue
                selected_seen.add(key)
                selected_articles.append(article)

        patched_plan = dict(ai_plan)
        patched_plan["topics"] = topics
        patched_plan["selected_articles"] = selected_articles

        if len(topics) < min_topics:
            logger.warning(
                "最低成稿目标为 %s 篇，但当前仅可产出 %s 篇（候选不足）。",
                min_topics,
                len(topics),
            )
        else:
            logger.info("已满足最低成稿目标：%s 篇。", len(topics))

        return patched_plan



    def run_news_generation(self) -> Dict[str, Any]:

        records, source_name = self.collect_articles(self.date, self.source)
        if not records:
            logger.warning(
                "未找到 %s 的可用文章数据。请先运行抓取脚本或检查数据文件。", self.date
            )
            return {
                "success": False,
                "reason": "no_records",
                "date": self.date,
                "draft_files": [],
            }

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        output_md = self.output_md or os.path.join(
            OUTPUT_DIR, f"ai_news_brief_{self.date}.md"
        )
        output_txt = self.output_txt or os.path.join(
            OUTPUT_DIR, f"ai_news_script_{self.date}.txt"
        )

        mode_used = self.topic_mode
        draft_files: List[str] = []
        if self.topic_mode == "ai":
            try:
                ai_plan = self.choose_topics_with_ai(records, args, self.date)
                ai_plan = self.ensure_minimum_ai_topics(
                    ai_plan=ai_plan,
                    records=records,
                    min_topics=getattr(self, "min_draft_topics", 0),
                )
                if not self.skip_drafts:
                    draft_files = self.generate_topic_drafts(self.date, ai_plan, args)

                broadcast_script = self.build_ai_broadcast_script(self.date, ai_plan, self.max_items)
                markdown_report = self.build_ai_markdown_report(
                    date_str=self.date,
                    source_name=source_name,
                    records=records,
                    ai_plan=ai_plan,
                    max_items=self.max_items,
                    draft_files=draft_files,
                )
                total_selected = len(ai_plan.get("selected_articles", []))
                total_topics = len(ai_plan.get("topics", []))
                stats_line = (
                    f"统计：总计 {len(records)} 条，AI 输入 {ai_plan.get('candidate_count', 0)} 条，"
                    f"AI选题 {total_topics} 个，入选文章 {total_selected} 条，生成成稿 {len(draft_files)} 篇。"
                )
            except Exception as error:
                if self.no_fallback:
                    logger.error("AI 选题失败：%s", error)
                    return {
                        "success": False,
                        "reason": "ai_selection_failed",
                        "error": self.safe_text(error),
                        "date": self.date,
                        "draft_files": [],
                    }

                logger.warning("AI 选题失败，自动回退到关键字模式：%s", error)
                mode_used = "keyword-fallback"

                ai_related, news_related, ai_news, ai_only, news_only = self.build_keyword_selection(
                    records
                )
                broadcast_script = self.build_keyword_broadcast_script(
                    date_str=self.date,
                    total_count=len(records),
                    news_count=len(news_related),
                    ai_count=len(ai_related),
                    ai_news=ai_news,
                    ai_only=ai_only,
                    news_only=news_only,
                    max_items=self.max_items,
                )
                markdown_report = self.build_keyword_markdown_report(
                    date_str=self.date,
                    source_name=source_name,
                    records=records,
                    ai_news=ai_news,
                    ai_only=ai_only,
                    news_only=news_only,
                    max_items=self.max_items,
                    broadcast_script=broadcast_script,
                )
                stats_line = (
                    "统计："
                    f"总计 {len(records)} 条，新闻 {len(news_related)} 条，AI {len(ai_related)} 条，交叉 {len(ai_news)} 条。"
                )
        else:
            ai_related, news_related, ai_news, ai_only, news_only = self.build_keyword_selection(records)
            broadcast_script = self.build_keyword_broadcast_script(
                date_str=self.date,
                total_count=len(records),
                news_count=len(news_related),
                ai_count=len(ai_related),
                ai_news=ai_news,
                ai_only=ai_only,
                news_only=news_only,
                max_items=self.max_items,
            )
            markdown_report = self.build_keyword_markdown_report(
                date_str=self.date,
                source_name=source_name,
                records=records,
                ai_news=ai_news,
                ai_only=ai_only,
                news_only=news_only,
                max_items=self.max_items,
                broadcast_script=broadcast_script,
            )
            stats_line = (
                "统计："
                f"总计 {len(records)} 条，新闻 {len(news_related)} 条，AI {len(ai_related)} 条，交叉 {len(ai_news)} 条。"
            )

        with open(output_md, "w", encoding="utf-8") as file:
            file.write(markdown_report)

        with open(output_txt, "w", encoding="utf-8") as file:
            file.write(broadcast_script + "\n")

        logger.info("已生成 Markdown 提取稿：%s", output_md)
        logger.info("已生成文字稿：%s", output_txt)
        logger.info("模式：%s", mode_used)
        logger.info(stats_line)
        if draft_files:
            logger.info("选题成稿目录：%s", os.path.dirname(draft_files[0]))

        return {
            "success": True,
            "reason": "ok",
            "date": self.date,
            "mode": mode_used,
            "records_count": len(records),
            "output_md": output_md,
            "output_txt": output_txt,
            "draft_files": draft_files,
            "draft_dir": os.path.dirname(draft_files[0]) if draft_files else "",
        }




def run_news_generation(args) -> dict:
    """Top-level entry point (backward compatible with content_workflow.py)."""
    return NewsGenerator(args).run_news_generation()

def parse_args():
    today_str = datetime.now().strftime("%Y-%m-%d")
    parser = argparse.ArgumentParser(
        description="根据当天微信公众号抓取结果，提取新闻和AI内容并生成文字稿。"
    )
    parser.add_argument(
        "--date",
        default=today_str,
        help=f"目标日期，格式 YYYY-MM-DD，默认今天（{today_str}）。",
    )
    parser.add_argument(
        "--source",
        choices=["auto", "report", "history"],
        default="auto",
        help="数据源：优先 report（当天报告）、history（历史库）或 auto（默认，先 report 再 history）。",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=12,
        help="每个分组最多写入的条目数，默认 12。",
    )
    parser.add_argument(
        "--topic-mode",
        choices=["ai", "keyword"],
        default="ai",
        help="选题模式：ai（默认，调用大模型选题）或 keyword（关键字规则）。",
    )
    parser.add_argument(
        "--max-topics",
        type=int,
        default=5,
        help="AI 模式下最多选题数，默认 5。",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=250,
        help="AI 模式下传给模型的候选文章上限，默认 250。",
    )
    parser.add_argument(
        "--skip-drafts",
        action="store_true",
        help="仅做选题，不自动生成每个选题的成稿。",
    )
    parser.add_argument(
        "--draft-model",
        default="",
        help="成稿模型名（默认与 --model 相同）。",
    )
    parser.add_argument(
        "--draft-dir",
        default="",
        help="成稿输出目录（默认 data/news_scripts/topic_drafts_YYYY-MM-DD）。",
    )
    parser.add_argument(
        "--max-draft-topics",
        type=int,
        default=5,
        help="最多生成多少篇选题成稿，默认 5。",
    )
    parser.add_argument(
        "--min-draft-topics",
        type=int,
        default=5,
        help="至少生成多少篇选题成稿，默认 5（会在 AI 选题不足时自动补位）。",
    )
    parser.add_argument(
        "--draft-target-words",
        type=int,
        default=1600,
        help="每篇成稿目标字数，默认 1600。",
    )
    parser.add_argument(
        "--min-imageholders",
        type=int,
        default=3,
        help="每篇文章至少包含多少个 IMAGEHOLDER，默认 3。",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_LLM_MODEL,
        help=f"AI 模型名，默认 {DEFAULT_LLM_MODEL}（可被 LLM_MODEL/DEEPSEEK_MODEL/OPENAI_MODEL 覆盖）。",
    )
    parser.add_argument(
        "--api-base",
        default=DEFAULT_LLM_BASE_URL,
        help=f"模型 API Base URL，默认 {DEFAULT_LLM_BASE_URL}（可被 LLM_API_BASE/DEEPSEEK_BASE_URL/OPENAI_BASE_URL 覆盖）。",
    )
    parser.add_argument(
        "--api-key",
        default=DEFAULT_LLM_API_KEY,
        help="模型 API Key（默认读取 LLM_API_KEY/DEEPSEEK_API_KEY/OPENAI_API_KEY）。",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="AI 模式失败时不回退到关键字模式。",
    )
    parser.add_argument(
        "--output-md",
        default="",
        help="输出 Markdown 路径（默认 data/news_scripts/ai_news_brief_YYYY-MM-DD.md）。",
    )
    parser.add_argument(
        "--output-txt",
        default="",
        help="输出文字稿路径（默认 data/news_scripts/ai_news_script_YYYY-MM-DD.txt）。",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    run_news_generation(args)



if __name__ == "__main__":
    main()

