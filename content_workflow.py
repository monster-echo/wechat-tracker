import asyncio
import logging
import types
from typing import Any, Dict, List

import attach_media_to_markdown
import generate_ai_news_script
from workflow_config import MediaGenerationConfig, TopicGenerationConfig

logger = logging.getLogger(__name__)


def _make_namespace(**kwargs) -> types.SimpleNamespace:
    """Create a simple namespace from keyword arguments."""
    return types.SimpleNamespace(**kwargs)


class TopicDraftWriter:
    def __init__(self, config: TopicGenerationConfig):
        self.config = config

    def _build_news_options(self, date_str: str) -> types.SimpleNamespace:
        max_topics = max(self.config.max_topics, self.config.min_draft_topics, 1)
        max_draft_topics = max(
            self.config.max_draft_topics,
            self.config.min_draft_topics,
            0,
        )
        return _make_namespace(
            date=date_str,
            source=self.config.source,
            max_items=self.config.max_items,
            topic_mode=self.config.topic_mode,
            max_topics=max_topics,
            max_candidates=max(self.config.max_candidates, 1),
            skip_drafts=False,
            draft_model=self.config.draft_model,
            draft_dir=self.config.draft_dir,
            max_draft_topics=max_draft_topics,
            min_draft_topics=max(self.config.min_draft_topics, 0),
            draft_target_words=max(self.config.draft_target_words, 600),
            min_imageholders=max(self.config.min_imageholders, 1),
            model=self.config.model,
            api_base=self.config.api_base,
            api_key=self.config.api_key,
            no_fallback=self.config.no_fallback,
            output_md="",
            output_txt="",
        )

    async def write_daily_articles(self, date_str: str) -> Dict[str, Any]:
        if self.config.topic_mode == "ai" and not self.config.api_key:
            logger.error("未配置 AI API Key，跳过成稿生成。")
            return {"success": False, "reason": "missing_api_key", "draft_files": []}

        opts = self._build_news_options(date_str)
        return await asyncio.to_thread(
            generate_ai_news_script.NewsGenerator(opts).run_news_generation,
        )


class MediaComposer:
    def __init__(self, config: MediaGenerationConfig):
        self.config = config

    def _build_media_options(self, markdown_file: str) -> types.SimpleNamespace:
        return _make_namespace(
            target=markdown_file,
            suffix=self.config.output_suffix,
            in_place=self.config.in_place,
            max_image_candidates=max(self.config.max_image_candidates, 0),
            max_video_candidates=max(self.config.max_video_candidates, 0),
            show_browser=self.config.show_browser,
            image_source=self.config.image_source,
            brand_style_mode=self.config.brand_style_mode,
            brand_style_name=self.config.brand_style_name,
            brand_style_cycle=self.config.brand_style_cycle,
            social_citation=self.config.social_citation,
            social_platforms=self.config.social_platforms,
            social_proxy=self.config.social_proxy,
            max_social_candidates=max(self.config.max_social_candidates, 0),
            social_shots_per_placeholder=max(self.config.social_shots_per_placeholder, 0),
            jimeng_base_url=self.config.jimeng_base_url,
            jimeng_session_id=self.config.jimeng_session_id,
            jimeng_model=self.config.jimeng_model,
            jimeng_ratio=self.config.jimeng_ratio,
            jimeng_resolution=self.config.jimeng_resolution,
            no_jimeng_fallback=self.config.no_jimeng_fallback,
        )

    async def compose_for_articles(self, markdown_files: List[str]) -> Dict[str, Any]:
        if not self.config.enabled:
            return {"enabled": False, "success_count": 0, "total": len(markdown_files)}

        success_count = 0
        for index, markdown_file in enumerate(markdown_files, 1):
            logger.info("配图处理中 [%s/%s]：%s", index, len(markdown_files), markdown_file)
            opts = self._build_media_options(markdown_file)
            try:
                await attach_media_to_markdown.MediaProcessor(opts).run()
                success_count += 1
            except Exception as error:
                logger.error("配图失败：%s -> %s", markdown_file, error)
        return {
            "enabled": True,
            "success_count": success_count,
            "total": len(markdown_files),
        }


class DailyContentPipeline:
    def __init__(
        self,
        topic_writer: TopicDraftWriter,
        media_composer: MediaComposer,
    ):
        self.topic_writer = topic_writer
        self.media_composer = media_composer

    async def run(self, date_str: str) -> Dict[str, Any]:
        logger.info(">>> 开始选话题与编写文章 (%s) <<<", date_str)
        generation_result = await self.topic_writer.write_daily_articles(date_str)
        if not generation_result or not generation_result.get("success"):
            logger.warning(
                "选话题与编写文章中止：%s",
                (generation_result or {}).get("reason", "unknown"),
            )
            return generation_result or {"success": False, "reason": "unknown"}

        draft_files = generation_result.get("draft_files", []) or []
        draft_count = len(draft_files)
        min_topics = self.topic_writer.config.min_draft_topics
        if draft_count < min_topics:
            logger.warning("当日成稿不足：%s/%s。", draft_count, min_topics)
        else:
            logger.info("当日成稿达标：%s/%s。", draft_count, min_topics)

        media_result = await self.media_composer.compose_for_articles(draft_files)
        generation_result["media_result"] = media_result
        logger.info("<<< 结束选话题与编写文章 (%s) >>>", date_str)
        return generation_result
