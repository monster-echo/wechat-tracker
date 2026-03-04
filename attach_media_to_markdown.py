import argparse
import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import httpx
from dotenv import load_dotenv
from playwright.async_api import async_playwright

import social_citation_module

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

IMAGEHOLDER_PATTERN = re.compile(r"\[IMAGEHOLDER\](.*?)\[/IMAGEHOLDER\]", re.DOTALL)
FIELD_PATTERNS = {
    "keywords": re.compile(r"-\s*搜索关键词[:：]\s*(.+)"),
    "description": re.compile(r"-\s*图片描述[:：]\s*(.+)"),
    "position_hint": re.compile(r"-\s*建议位置[:：]\s*(.+)"),
}

BRAND_STYLE_PRESETS: Dict[str, Dict[str, str]] = {
    "oracle_bone": {
        "label": "甲骨文科技",
        "prompt": (
            "整体采用甲骨文与青铜铭刻美学：龟甲裂纹、骨刻纹理、赭石与墨黑矿物色、古拙线条。"
            "可使用抽象甲骨符号作为纹样，不出现可识别现代文字。"
            "保持现代科技新闻构图与镜头感，画面要克制、高级、可读。"
        ),
    },
    "dunhuang_mural": {
        "label": "敦煌未来",
        "prompt": (
            "整体采用敦煌壁画美学：石青石绿赭红矿物色、金线勾勒、飞天云纹与壁画肌理。"
            "在古典国风语汇中融入现代科技场景，叙事感强，层次清晰。"
            "禁止可识别文字与水印。"
        ),
    },
    "steampunk": {
        "label": "蒸汽朋克",
        "prompt": (
            "整体采用蒸汽朋克复古未来美学：黄铜、齿轮、铆钉、蒸汽管道、机械仪表，"
            "暖棕与铜金主调，强机械细节与空间纵深。"
            "保留新闻插图可读性，禁止可识别文字与水印。"
        ),
    },
    "ink_illustration": {
        "label": "水墨插画",
        "prompt": (
            "整体采用现代水墨插画美学：墨韵晕染、留白构图、宣纸肌理、少量矿物色点缀。"
            "国风与科技融合，画面有叙事感，细节克制，禁止可识别文字与水印。"
        ),
    },
    "guochao_poster": {
        "label": "国潮海报",
        "prompt": (
            "整体采用国潮插画海报美学：高饱和矿物色、强图形轮廓、装饰纹样、戏剧化光影。"
            "在传统元素中表达现代科技议题，视觉冲击强，禁止可识别文字与水印。"
        ),
    },
    "paper_cut_collage": {
        "label": "剪纸拼贴",
        "prompt": (
            "整体采用剪纸与拼贴插画风格：纸张层叠、镂空轮廓、手作边缘、纹理分层。"
            "抽象表达科技场景，保持主体明确与新闻可读性，禁止可识别文字与水印。"
        ),
    },
    "woodcut_print": {
        "label": "木刻版画",
        "prompt": (
            "整体采用木刻版画风格：粗犷刀痕、黑白或低彩对比、复古印刷颗粒感。"
            "强调结构与节奏，营造纪实力量感，禁止可识别文字与水印。"
        ),
    },
    "editorial_illustration": {
        "label": "编辑插画",
        "prompt": (
            "整体采用国际科技媒体常见 editorial illustration 风格：概念隐喻清晰、构图简洁、"
            "色彩统一、视觉语言高级。强调观点表达与信息清晰，禁止可识别文字与水印。"
        ),
    },
    "flat_illustration": {
        "label": "扁平插画",
        "prompt": (
            "整体采用扁平化矢量插画风格：几何图形、清晰轮廓、低噪点、模块化构图。"
            "适合科技解读类内容，确保阅读友好，禁止可识别文字与水印。"
        ),
    },
    "isometric_infographic": {
        "label": "等距信息插画",
        "prompt": (
            "整体采用等距视角信息插画风格：3D 等距结构、信息模块分区、整洁科技配色。"
            "强调系统关系和流程感，便于解释复杂主题，禁止可识别文字与水印。"
        ),
    },
    "retro_scifi_comic": {
        "label": "复古科幻漫画",
        "prompt": (
            "整体采用复古科幻漫画插画风格：颗粒纸感、分镜叙事、强对比配色、轻戏剧张力。"
            "保留科技新闻可读性，不要夸张失真，禁止可识别文字与水印。"
        ),
    },
}
BRAND_STYLE_ALIASES: Dict[str, str] = {
    "oracle_bone": "oracle_bone",
    "oracle": "oracle_bone",
    "jiaguwen": "oracle_bone",
    "甲骨文": "oracle_bone",
    "甲骨文风": "oracle_bone",
    "甲骨文风格": "oracle_bone",
    "dunhuang_mural": "dunhuang_mural",
    "dunhuang": "dunhuang_mural",
    "敦煌": "dunhuang_mural",
    "敦煌风": "dunhuang_mural",
    "敦煌风格": "dunhuang_mural",
    "steampunk": "steampunk",
    "蒸汽朋克": "steampunk",
    "蒸汽朋克风": "steampunk",
    "蒸汽朋克风格": "steampunk",
    "ink_illustration": "ink_illustration",
    "ink": "ink_illustration",
    "水墨": "ink_illustration",
    "水墨风": "ink_illustration",
    "水墨插画": "ink_illustration",
    "guochao_poster": "guochao_poster",
    "guochao": "guochao_poster",
    "国潮": "guochao_poster",
    "国潮风": "guochao_poster",
    "国潮海报": "guochao_poster",
    "paper_cut_collage": "paper_cut_collage",
    "papercut": "paper_cut_collage",
    "剪纸": "paper_cut_collage",
    "剪纸风": "paper_cut_collage",
    "剪纸拼贴": "paper_cut_collage",
    "woodcut_print": "woodcut_print",
    "woodcut": "woodcut_print",
    "木刻": "woodcut_print",
    "木刻风": "woodcut_print",
    "木刻版画": "woodcut_print",
    "editorial_illustration": "editorial_illustration",
    "editorial": "editorial_illustration",
    "illustration": "editorial_illustration",
    "插画": "editorial_illustration",
    "插画风": "editorial_illustration",
    "编辑插画": "editorial_illustration",
    "杂志插画": "editorial_illustration",
    "flat_illustration": "flat_illustration",
    "flat": "flat_illustration",
    "扁平": "flat_illustration",
    "扁平风": "flat_illustration",
    "扁平插画": "flat_illustration",
    "isometric_infographic": "isometric_infographic",
    "isometric": "isometric_infographic",
    "等距": "isometric_infographic",
    "等距风": "isometric_infographic",
    "等距插画": "isometric_infographic",
    "retro_scifi_comic": "retro_scifi_comic",
    "retro_comic": "retro_scifi_comic",
    "复古科幻": "retro_scifi_comic",
    "复古科幻漫画": "retro_scifi_comic",
    "漫画风": "retro_scifi_comic",
}
DEFAULT_BRAND_STYLE_CYCLE = (
    "oracle_bone,dunhuang_mural,steampunk,"
    "ink_illustration,guochao_poster,paper_cut_collage,"
    "woodcut_print,editorial_illustration,flat_illustration,"
    "isometric_infographic,retro_scifi_comic"
)


@dataclass
class Placeholder:
    index: int
    start: int
    end: int
    raw_block: str
    keywords: str
    description: str
    position_hint: str
    query: str


@dataclass
class MediaSelection:
    image_url: str = ""
    image_source: str = ""
    image_title: str = ""
    image_local_path: str = ""
    brand_style_key: str = ""
    brand_style_label: str = ""
    video_url: str = ""
    video_source: str = ""
    video_title: str = ""
    generated_with_jimeng: bool = False
    social_screenshots: Optional[List[Dict[str, str]]] = None
    image_candidates: Optional[List[Dict[str, str]]] = None
    video_candidates: Optional[List[Dict[str, str]]] = None
    notes: Optional[str] = None

class MediaProcessor:
    def __init__(self, args) -> None:
        """Initialise MediaProcessor from a runtime-options object.
        All properties are stored on self so methods don't need to thread `args`.
        """
        self.target: str = args.target
        self.suffix: str = getattr(args, "suffix", "_media")
        self.in_place: bool = getattr(args, "in_place", False)
        self.max_image_candidates: int = getattr(args, "max_image_candidates", 10)
        self.max_video_candidates: int = getattr(args, "max_video_candidates", 0)
        self.show_browser: bool = getattr(args, "show_browser", False)
        self.image_source: str = getattr(args, "image_source", "bing")
        self.brand_style_mode: str = getattr(args, "brand_style_mode", "fixed")
        self.brand_style_name: str = getattr(args, "brand_style_name", "dunhuang_mural")
        self.brand_style_cycle: str = getattr(args, "brand_style_cycle", "")
        self.social_citation: str = getattr(args, "social_citation", "off")
        self.social_platforms: str = getattr(args, "social_platforms", "reddit,x")
        self.social_proxy: str = getattr(args, "social_proxy", "")
        self.max_social_candidates: int = getattr(args, "max_social_candidates", 6)
        self.social_shots_per_placeholder: int = getattr(args, "social_shots_per_placeholder", 1)
        self.jimeng_base_url: str = getattr(args, "jimeng_base_url", "")
        self.jimeng_session_id: str = getattr(args, "jimeng_session_id", "")
        self.jimeng_model: str = getattr(args, "jimeng_model", "jimeng-4.5")
        self.jimeng_ratio: str = getattr(args, "jimeng_ratio", "16:9")
        self.jimeng_resolution: str = getattr(args, "jimeng_resolution", "2k")
        self.no_jimeng_fallback: bool = getattr(args, "no_jimeng_fallback", False)

    def sanitize_filename(self, filename: str, max_length: int = 96) -> str:
        clean_name = re.sub(r'[\\/*?:"<>|]', "", str(filename).strip())
        clean_name = re.sub(r"\s+", "_", clean_name)
        clean_name = clean_name.strip("._")
        if not clean_name:
            return "asset"
        return clean_name[:max_length]



    def find_markdown_files(self, target: str) -> List[Path]:
        target_path = Path(target)
        if not target_path.exists():
            raise FileNotFoundError(f"目标路径不存在：{target_path}")

        if target_path.is_file():
            return [target_path]

        files = [
            file_path
            for file_path in target_path.rglob("*.md")
            if not file_path.name.startswith("._")
        ]
        return sorted(files)



    def extract_placeholder_field(self, block_inner: str, field_name: str) -> str:
        pattern = FIELD_PATTERNS[field_name]
        match = pattern.search(block_inner)
        if not match:
            return ""
        return match.group(1).strip()



    def normalize_keywords(self, raw_keywords: str) -> str:
        if not raw_keywords:
            return ""
        parts = [part.strip() for part in re.split(r"[，,、;；]", raw_keywords) if part.strip()]
        return " ".join(parts)



    def build_query(self, keywords: str, description: str) -> str:
        keyword_query = self.normalize_keywords(keywords)
        if keyword_query and description:
            return f"{keyword_query} {description[:30]}"
        if keyword_query:
            return keyword_query
        if description:
            return description[:40]
        return "科技 新闻 现场"



    def build_social_query(self, placeholder: Placeholder) -> str:
        return social_citation_module.self.build_social_query(placeholder)



    def parse_placeholders(self, markdown_text: str) -> List[Placeholder]:
        placeholders = []
        for index, match in enumerate(IMAGEHOLDER_PATTERN.finditer(markdown_text), 1):
            raw_block = match.group(0)
            inner = match.group(1)
            keywords = self.extract_placeholder_field(inner, "keywords")
            description = self.extract_placeholder_field(inner, "description")
            position_hint = self.extract_placeholder_field(inner, "position_hint")
            query = self.build_query(keywords, description)

            placeholders.append(
                Placeholder(
                    index=index,
                    start=match.start(),
                    end=match.end(),
                    raw_block=raw_block,
                    keywords=keywords,
                    description=description,
                    position_hint=position_hint,
                    query=query,
                )
            )
        return placeholders



    def normalize_brand_style_key(self, raw_style: str) -> str:
        raw = (raw_style or "").strip()
        if not raw:
            return ""

        candidates = [
            raw,
            raw.lower(),
            raw.replace("-", "_"),
            raw.lower().replace("-", "_"),
            raw.replace(" ", "_"),
            raw.lower().replace(" ", "_"),
        ]
        for candidate in candidates:
            if candidate in BRAND_STYLE_PRESETS:
                return candidate
            mapped = BRAND_STYLE_ALIASES.get(candidate)
            if mapped in BRAND_STYLE_PRESETS:
                return mapped
        return ""



    def parse_brand_style_cycle(self, raw_cycle: str) -> List[str]:
        style_keys: List[str] = []
        for item in (raw_cycle or "").split(","):
            key = self.normalize_brand_style_key(item)
            if not key:
                continue
            if key in style_keys:
                continue
            style_keys.append(key)
        return style_keys or list(BRAND_STYLE_PRESETS.keys())



    def choose_brand_style_for_article(self, file_path: Path, args) -> Tuple[str, Dict[str, str]]:
        style_keys = self.parse_brand_style_cycle(self.brand_style_cycle)
        fixed_style = self.normalize_brand_style_key(self.brand_style_name)

        if self.brand_style_mode == "fixed":
            selected_key = fixed_style or style_keys[0]
        elif self.brand_style_mode == "daily_rotate":
            seed = datetime.now().strftime("%Y-%m-%d")
            index = int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:8], 16) % len(style_keys)
            selected_key = style_keys[index]
        else:
            seed = file_path.stem or datetime.now().strftime("%Y-%m-%d")
            index = int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:8], 16) % len(style_keys)
            selected_key = style_keys[index]

        return selected_key, BRAND_STYLE_PRESETS[selected_key]



    def parse_social_platforms(self, raw_platforms: str) -> List[str]:
        return social_citation_module.self.parse_social_platforms(raw_platforms)



    def should_capture_social(self, placeholder: Placeholder, args) -> bool:
        return social_citation_module.self.should_capture_social(placeholder, args)



    def normalize_social_url(self, url: str, platform: str) -> str:
        return social_citation_module.self.normalize_social_url(url, platform)



    async def goto_with_redirect_tolerance(self, 
        page,
        url: str,
        timeout_ms: int = social_citation_module.SOCIAL_NAV_TIMEOUT_MS,
    ) -> Tuple[bool, str]:
        return await social_citation_module.self.goto_with_redirect_tolerance(page, url, timeout_ms)



    async def search_social_posts_via_bing(self, 
        page, query: str, platform: str, max_results: int
    ) -> List[Dict[str, str]]:
        return await social_citation_module.self.search_social_posts_via_bing(
            page,
            query,
            platform,
            max_results,
        )



    async def search_reddit_posts_direct(self, page, query: str, max_results: int) -> List[Dict[str, str]]:
        return await social_citation_module.self.search_reddit_posts_direct(page, query, max_results)



    async def capture_social_post_screenshot(self, 
        page, url: str, platform: str, save_path: Path
    ) -> Tuple[bool, str, str]:
        return await social_citation_module.self.capture_social_post_screenshot(
            page,
            url,
            platform,
            save_path,
        )



    async def capture_platform_search_results_screenshot(self, 
        page, query: str, platform: str, save_path: Path
    ) -> Tuple[bool, str, str]:
        return await social_citation_module.self.capture_platform_search_results_screenshot(
            page,
            query,
            platform,
            save_path,
        )



    async def collect_social_screenshots(self, 
        page,
        placeholder: Placeholder,
        args,
        assets_dir: Path,
        file_stem: str,
        output_parent: Path,
    ) -> List[Dict[str, str]]:
        return await social_citation_module.self.collect_social_screenshots(
            page=page,
            placeholder=placeholder,
            args=args,
            assets_dir=assets_dir,
            file_stem=file_stem,
            output_parent=output_parent,
            logger=logger,
        )



    def contains_keywords(self, text: str, query: str) -> int:
        if not text:
            return 0
        tokens = [token for token in query.lower().split() if len(token) > 1]
        if not tokens:
            return 0
        lowered = text.lower()
        return sum(1 for token in tokens if token in lowered)



    def is_valid_http_url(self, url: str) -> bool:
        return bool(url and (url.startswith("http://") or url.startswith("https://")))



    async def search_bing_images(self, page, query: str, max_results: int) -> List[Dict[str, str]]:
        search_url = f"https://www.bing.com/images/search?q={quote_plus(query)}&form=HDRSC2"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1800)
        results = await page.evaluate(
            """
            (limit) => {
              const pushUnique = (list, item) => {
                if (!item || !item.url || !/^https?:\\/\\//.test(item.url)) return;
                if (item.url.startsWith('data:')) return;
                if (list.some((x) => x.url === item.url)) return;
                list.push(item);
              };

              const items = [];
              for (const node of document.querySelectorAll('a.iusc')) {
                try {
                  const raw = node.getAttribute('m');
                  if (!raw) continue;
                  const meta = JSON.parse(raw);
                  const url = meta.murl || meta.turl || '';
                  const title = node.getAttribute('title') || meta.t || '';
                  pushUnique(items, { url, title, source: 'bing-images' });
                } catch (_) {}
                if (items.length >= limit) break;
              }

              if (items.length < limit) {
                for (const img of document.querySelectorAll('img.mimg')) {
                  const url = img.src || '';
                  const title = img.alt || '';
                  pushUnique(items, { url, title, source: 'bing-images-thumb' });
                  if (items.length >= limit) break;
                }
              }
              return items.slice(0, limit);
            }
            """,
            max_results,
        )
        return results or []



    async def search_pexels_images(self, page, query: str, max_results: int) -> List[Dict[str, str]]:
        search_url = f"https://www.pexels.com/search/{quote_plus(query)}/"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1800)
        results = await page.evaluate(
            """
            (limit) => {
              const pushUnique = (list, item) => {
                if (!item || !item.url || !/^https?:\\/\\//.test(item.url)) return;
                if (item.url.startsWith('data:')) return;
                if (list.some((x) => x.url === item.url)) return;
                list.push(item);
              };

              const items = [];
              for (const img of document.querySelectorAll('img[src], img[srcset]')) {
                const src = img.getAttribute('src') || '';
                const srcSet = img.getAttribute('srcset') || '';
                const firstSrcSet = srcSet ? srcSet.split(',')[0].trim().split(' ')[0] : '';
                const url = src || firstSrcSet || '';
                const title = img.getAttribute('alt') || '';
                if (!/images\\.pexels\\.com/.test(url)) continue;
                pushUnique(items, { url, title, source: 'pexels-images' });
                if (items.length >= limit) break;
              }
              return items.slice(0, limit);
            }
            """,
            max_results,
        )
        return results or []



    async def search_pexels_videos(self, page, query: str, max_results: int) -> List[Dict[str, str]]:
        search_url = f"https://www.pexels.com/search/videos/{quote_plus(query)}/"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(2200)
        results = await page.evaluate(
            """
            (limit) => {
              const pushUnique = (list, item) => {
                if (!item || !item.url || !/^https?:\\/\\//.test(item.url)) return;
                if (list.some((x) => x.url === item.url)) return;
                list.push(item);
              };
              const items = [];
              for (const a of document.querySelectorAll('a[href*="/video/"]')) {
                const href = a.href || '';
                const title = (a.getAttribute('title') || a.innerText || '').trim().slice(0, 120);
                if (!/\\/video\\//.test(href)) continue;
                pushUnique(items, { url: href, title, source: 'pexels-videos' });
                if (items.length >= limit) break;
              }
              return items.slice(0, limit);
            }
            """,
            max_results,
        )
        return results or []



    async def search_bing_videos(self, page, query: str, max_results: int) -> List[Dict[str, str]]:
        search_url = f"https://www.bing.com/videos/search?q={quote_plus(query)}"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1800)
        results = await page.evaluate(
            """
            (limit) => {
              const pushUnique = (list, item) => {
                if (!item || !item.url || !/^https?:\\/\\//.test(item.url)) return;
                if (list.some((x) => x.url === item.url)) return;
                list.push(item);
              };
              const items = [];
              const selectors = ['a.mc_vtvc_link', 'a[href*="youtube.com"]', 'a[href*="bilibili.com"]', 'a[href*="vimeo.com"]'];
              for (const selector of selectors) {
                for (const node of document.querySelectorAll(selector)) {
                  const url = node.href || '';
                  const title = (node.getAttribute('title') || node.textContent || '').trim().slice(0, 120);
                  pushUnique(items, { url, title, source: 'bing-videos' });
                  if (items.length >= limit) return items.slice(0, limit);
                }
              }
              return items.slice(0, limit);
            }
            """,
            max_results,
        )
        return results or []



    def merge_candidates(self, *candidate_lists: List[Dict[str, str]]) -> List[Dict[str, str]]:
        merged = []
        seen = set()
        for candidate_list in candidate_lists:
            for candidate in candidate_list:
                url = candidate.get("url", "").strip()
                if not url or url in seen:
                    continue
                if not url.startswith("http://") and not url.startswith("https://"):
                    continue
                seen.add(url)
                merged.append(candidate)
        return merged



    async def validate_image_candidate(self, 
        client: httpx.AsyncClient, candidate: Dict[str, str]
    ) -> bool:
        url = candidate.get("url", "")
        if not url.startswith("http://") and not url.startswith("https://"):
            return False

        lowered = url.lower().split("?")[0]
        if lowered.endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")):
            return True

        try:
            response = await client.head(url, follow_redirects=True, timeout=20.0)
            if response.status_code >= 400:
                return False
            content_type = response.headers.get("content-type", "").lower()
            if "image/" in content_type:
                return True
            if response.status_code in (405, 403):
                stream = await client.get(
                    url,
                    follow_redirects=True,
                    timeout=20.0,
                    headers={"Range": "bytes=0-1024"},
                )
                stream_type = stream.headers.get("content-type", "").lower()
                return "image/" in stream_type
        except Exception:
            return False

        return False



    def pick_video_candidate(self, candidates: List[Dict[str, str]], query: str) -> Dict[str, str]:
        if not candidates:
            return {}

        scored = sorted(
            candidates,
            key=lambda item: (
                -self.contains_keywords(item.get("title", ""), query),
                "pexels" not in item.get("source", ""),
            ),
        )
        return scored[0]



    async def pick_image_candidate(self, 
        client: httpx.AsyncClient, candidates: List[Dict[str, str]], query: str
    ) -> Dict[str, str]:
        if not candidates:
            return {}

        scored = sorted(
            candidates,
            key=lambda item: (
                -self.contains_keywords(item.get("title", ""), query),
                "thumb" in item.get("source", ""),
            ),
        )
        for candidate in scored:
            if await self.validate_image_candidate(client, candidate):
                return candidate
        return {}



    def build_jimeng_prompt(self, placeholder: Placeholder, brand_style: Optional[Dict[str, str]] = None) -> str:
        text_parts = []
        if brand_style:
            style_label = (brand_style.get("label", "") or "").strip()
            style_prompt = (brand_style.get("prompt", "") or "").strip()
            if style_label:
                text_parts.append(f"风格锚点（全稿统一）：{style_label}")
            if style_prompt:
                text_parts.append(f"风格要求（高优先级）：{style_prompt}")
        if placeholder.keywords:
            text_parts.append(placeholder.keywords)
        if placeholder.description:
            text_parts.append(placeholder.description)
        text_parts.append("科技新闻图文配图，品牌一致性构图，高清细节，不要可识别文字、水印、logo")
        return "，".join(text_parts)



    async def generate_image_with_jimeng(self, 
        client: httpx.AsyncClient, placeholder: Placeholder, args, brand_style: Optional[Dict[str, str]] = None
    ) -> Tuple[str, str]:
        if self.no_jimeng_fallback:
            return "", "已禁用即梦回退"

        if not self.jimeng_session_id:
            return "", "缺少 JIMENG_SESSION_ID，无法调用即梦回退"

        base_url = self.jimeng_base_url.rstrip("/")
        request_url = f"{base_url}/v1/images/generations"
        payload = {
            "model": self.jimeng_model,
            "prompt": self.build_jimeng_prompt(placeholder, brand_style),
            "ratio": self.jimeng_ratio,
            "resolution": self.jimeng_resolution,
            "response_format": "url",
        }
        headers = {
            "Authorization": f"Bearer {self.jimeng_session_id}",
            "Content-Type": "application/json",
        }

        try:
            response = await client.post(
                request_url,
                headers=headers,
                json=payload,
                timeout=900.0,
            )
        except Exception as error:
            return "", f"即梦请求失败：{error}"

        try:
            data = response.json()
        except Exception:
            return "", f"即梦响应非 JSON（HTTP {response.status_code}）"

        if response.status_code >= 400:
            return "", f"即梦请求失败（HTTP {response.status_code}）：{data}"

        if isinstance(data, dict) and data.get("code") not in (None, 0):
            return "", f"即梦返回错误：{data.get('message', data.get('code'))}"

        image_url = ""
        if isinstance(data, dict):
            items = data.get("data") or []
            if isinstance(items, list) and items:
                first = items[0]
                if isinstance(first, dict):
                    image_url = first.get("url", "")

        if not image_url:
            return "", f"即梦返回中未发现图片 URL：{data}"

        return image_url, ""



    def infer_extension(self, url: str, content_type: str) -> str:
        if content_type:
            guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
            if guessed in (".jpe", ".jpeg"):
                return ".jpg"
            if guessed:
                return guessed

        lowered = url.lower().split("?")[0]
        for extension in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
            if lowered.endswith(extension):
                return ".jpg" if extension == ".jpeg" else extension
        return ".jpg"



    async def download_image(self, 
        client: httpx.AsyncClient, image_url: str, save_path_without_ext: Path
    ) -> Optional[Path]:
        try:
            response = await client.get(image_url, follow_redirects=True, timeout=60.0)
        except Exception as error:
            logger.warning("下载图片失败：%s (%s)", image_url, error)
            return None

        if response.status_code >= 400:
            logger.warning("下载图片失败（HTTP %s）：%s", response.status_code, image_url)
            return None

        content_type = response.headers.get("content-type", "")
        if "image/" not in content_type.lower():
            logger.warning("下载内容非图片：%s (%s)", image_url, content_type)
            return None

        extension = self.infer_extension(image_url, content_type)
        save_path = Path(f"{save_path_without_ext}{extension}")
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(response.content)
        return save_path



    def build_media_block(self, placeholder: Placeholder, selection: MediaSelection, image_ref: str) -> str:
        description = placeholder.description or placeholder.keywords or "配图"
        lines = []

        if image_ref:
            lines.append(f"![{description}]({image_ref})")
        else:
            lines.append(f"> [配图待补充] 关键词：{placeholder.keywords or placeholder.query}")

        lines.append("")
        if selection.video_url:
            video_title = selection.video_title or "相关视频素材"
            lines.append(f"> 视频参考：[{video_title}]({selection.video_url})")

        if selection.social_screenshots:
            for social in selection.social_screenshots:
                platform_name = social.get("platform", "").upper() or "SOCIAL"
                shot_ref = social.get("screenshot_ref", "")
                source_url = social.get("source_url", "")
                title = social.get("title", "") or f"{platform_name} 帖子"
                if shot_ref:
                    lines.append(f"![{platform_name} 引用截图]({shot_ref})")
                if source_url:
                    lines.append(f"> 社交引用：[{title}]({source_url})（{platform_name}）")

        if selection.generated_with_jimeng:
            lines.append(f"> 图片来源：即梦文生图（{selection.image_source or 'jimeng'}）")
        elif selection.image_source:
            lines.append(f"> 图片来源：{selection.image_source}")
        if selection.brand_style_label:
            lines.append(f"> 视觉风格：{selection.brand_style_label}")

        if placeholder.keywords:
            lines.append(f"> 检索关键词：{placeholder.keywords}")
        if placeholder.position_hint:
            lines.append(f"> 原占位建议：{placeholder.position_hint}")

        if selection.notes:
            lines.append(f"> 备注：{selection.notes}")

        return "\n".join(lines).strip()



    async def search_image_candidates(self, 
        page, placeholder: Placeholder, args
    ) -> List[Dict[str, str]]:
        image_candidates = []
        if self.max_image_candidates <= 0:
            return image_candidates

        try:
            bing_images = await self.search_bing_images(page, placeholder.query, self.max_image_candidates)
        except Exception as error:
            logger.warning("Bing 图片检索失败（占位 #%s）：%s", placeholder.index, error)
            bing_images = []

        try:
            pexels_images = await self.search_pexels_images(
                page, placeholder.query, self.max_image_candidates
            )
        except Exception as error:
            logger.warning("Pexels 图片检索失败（占位 #%s）：%s", placeholder.index, error)
            pexels_images = []

        image_candidates = self.merge_candidates(bing_images, pexels_images)
        return image_candidates[: self.max_image_candidates]



    async def search_video_candidates(self, 
        page, placeholder: Placeholder, args
    ) -> List[Dict[str, str]]:
        video_candidates = []
        if self.max_video_candidates <= 0:
            return video_candidates

        try:
            pexels_videos = await self.search_pexels_videos(
                page, placeholder.query, self.max_video_candidates
            )
        except Exception as error:
            logger.warning("Pexels 视频检索失败（占位 #%s）：%s", placeholder.index, error)
            pexels_videos = []

        try:
            bing_videos = await self.search_bing_videos(page, placeholder.query, self.max_video_candidates)
        except Exception as error:
            logger.warning("Bing 视频检索失败（占位 #%s）：%s", placeholder.index, error)
            bing_videos = []

        video_candidates = self.merge_candidates(pexels_videos, bing_videos)
        return video_candidates[: self.max_video_candidates]



    async def collect_media_for_placeholder(self, 
        page,
        client: httpx.AsyncClient,
        placeholder: Placeholder,
        args,
        article_style_key: str,
        article_style: Dict[str, str],
    ) -> MediaSelection:
        logger.info(
            "占位 #%s：图片策略=%s，关键词=%s",
            placeholder.index,
            self.image_source,
            placeholder.query,
        )

        video_candidates = await self.search_video_candidates(page, placeholder, args)
        best_video = self.pick_video_candidate(video_candidates, placeholder.query)

        selection = MediaSelection(
            brand_style_key=article_style_key,
            brand_style_label=article_style.get("label", article_style_key),
            video_url=best_video.get("url", ""),
            video_source=best_video.get("source", ""),
            video_title=best_video.get("title", ""),
            video_candidates=video_candidates,
            image_candidates=[],
        )

        async def try_jimeng() -> Tuple[bool, str]:
            generated_url, error_message = await self.generate_image_with_jimeng(
                client, placeholder, args, brand_style=article_style
            )
            if generated_url:
                selection.image_url = generated_url
                selection.image_source = self.jimeng_model
                selection.generated_with_jimeng = True
                return True, ""
            return False, error_message or "即梦回退失败"

        async def try_search() -> Tuple[bool, str]:
            image_candidates = await self.search_image_candidates(page, placeholder, args)
            selection.image_candidates = image_candidates
            best_image = await self.pick_image_candidate(client, image_candidates, placeholder.query)
            if best_image:
                selection.image_url = best_image.get("url", "")
                selection.image_source = best_image.get("source", "")
                selection.image_title = best_image.get("title", "")
                selection.generated_with_jimeng = False
                return True, ""
            return False, "未检索到可用图片"

        strategy = self.image_source
        error_notes: List[str] = []

        if strategy == "jimeng":
            success, note = await try_jimeng()
            if not success and note:
                error_notes.append(note)
        elif strategy == "search":
            success, note = await try_search()
            if not success and note:
                error_notes.append(note)
        elif strategy == "search_then_jimeng":
            success, note = await try_search()
            if not success and note:
                error_notes.append(note)
                success, note = await try_jimeng()
                if not success and note:
                    error_notes.append(note)
        elif strategy == "jimeng_then_search":
            success, note = await try_jimeng()
            if not success and note:
                error_notes.append(note)
                success, note = await try_search()
                if not success and note:
                    error_notes.append(note)
        else:
            error_notes.append(f"未知策略：{strategy}")

        if not selection.image_url and error_notes:
            selection.notes = "；".join(dict.fromkeys(error_notes))

        return selection



    def render_markdown_with_media(self, 
        original_markdown: str,
        placeholders: List[Placeholder],
        rendered_blocks: Dict[int, str],
    ) -> str:
        output_parts = []
        cursor = 0
        for placeholder in placeholders:
            output_parts.append(original_markdown[cursor : placeholder.start])
            output_parts.append(rendered_blocks.get(placeholder.index, placeholder.raw_block))
            cursor = placeholder.end
        output_parts.append(original_markdown[cursor:])
        return "".join(output_parts)



    async def process_markdown_file(self, 
        file_path: Path,
        page,
        social_page,
        client: httpx.AsyncClient,
        args,
    ):
        original_markdown = file_path.read_text(encoding="utf-8")
        placeholders = self.parse_placeholders(original_markdown)
        if not placeholders:
            logger.info("文件未发现 IMAGEHOLDER，跳过：%s", file_path)
            return

        if self.in_place:
            output_file = file_path
        else:
            output_file = file_path.with_name(f"{file_path.stem}{self.suffix}{file_path.suffix}")

        assets_dir = output_file.parent / f"{output_file.stem}_assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        article_style_key, article_style = self.choose_brand_style_for_article(file_path, args)
        logger.info("开始处理：%s（占位 %s 个）", file_path, len(placeholders))
        logger.info(
            "文章风格：%s（%s）",
            article_style.get("label", article_style_key),
            article_style_key,
        )

        rendered_blocks = {}
        manifest_items = []
        for placeholder in placeholders:
            selection = await self.collect_media_for_placeholder(
                page=page,
                client=client,
                placeholder=placeholder,
                args=args,
                article_style_key=article_style_key,
                article_style=article_style,
            )

            local_image_path = ""
            image_ref = selection.image_url
            if selection.image_url:
                image_basename = f"{self.sanitize_filename(file_path.stem)}_ph{placeholder.index:02d}"
                downloaded_path = await self.download_image(
                    client, selection.image_url, assets_dir / image_basename
                )
                if downloaded_path:
                    selection.image_local_path = str(downloaded_path)
                    local_image_path = os.path.relpath(downloaded_path, output_file.parent)
                    image_ref = local_image_path

            social_screenshots = []
            if self.social_citation != "off" and social_page is not None:
                social_screenshots = await self.collect_social_screenshots(
                    page=social_page,
                    placeholder=placeholder,
                    args=args,
                    assets_dir=assets_dir,
                    file_stem=file_path.stem,
                    output_parent=output_file.parent,
                )
            selection.social_screenshots = social_screenshots

            rendered_blocks[placeholder.index] = self.build_media_block(placeholder, selection, image_ref)

            manifest_items.append(
                {
                    "placeholder_index": placeholder.index,
                    "query": placeholder.query,
                    "keywords": placeholder.keywords,
                    "description": placeholder.description,
                    "position_hint": placeholder.position_hint,
                    "selected": {
                        "image_url": selection.image_url,
                        "image_source": selection.image_source,
                        "image_local_path": selection.image_local_path,
                        "brand_style_key": selection.brand_style_key,
                        "brand_style_label": selection.brand_style_label,
                        "video_url": selection.video_url,
                        "video_source": selection.video_source,
                        "video_title": selection.video_title,
                        "generated_with_jimeng": selection.generated_with_jimeng,
                        "social_screenshots": selection.social_screenshots or [],
                        "notes": selection.notes,
                    },
                    "candidates": {
                        "images": selection.image_candidates or [],
                        "videos": selection.video_candidates or [],
                    },
                }
            )

        new_markdown = self.render_markdown_with_media(original_markdown, placeholders, rendered_blocks)
        output_file.write_text(new_markdown, encoding="utf-8")

        manifest_path = output_file.with_suffix(".media.json")
        manifest = {
            "input_markdown": str(file_path),
            "output_markdown": str(output_file),
            "generated_at": datetime.now().isoformat(),
            "jimeng_base_url": self.jimeng_base_url,
            "article_style": {
                "key": article_style_key,
                "label": article_style.get("label", article_style_key),
                "prompt": article_style.get("prompt", ""),
            },
            "placeholders": manifest_items,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info("已生成配图稿：%s", output_file)
        logger.info("已生成素材清单：%s", manifest_path)



    async def run(self):
        files = self.find_markdown_files(self.target)
        if not files:
            logger.warning("没有找到 Markdown 文件：%s", self.target)
            return

        logger.info("共发现 %s 个 Markdown 文件待处理。", len(files))
        async with httpx.AsyncClient() as client:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=not self.show_browser)
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/123.0.0.0 Safari/537.36"
                    )
                )
                page = await context.new_page()

                social_browser = None
                social_context = None
                social_page = None
                social_enabled = self.social_citation != "off"
                if social_enabled:
                    social_page = page
                    if self.social_proxy:
                        logger.info("社交截图浏览使用代理：%s", self.social_proxy)
                        social_browser = await playwright.chromium.launch(
                            headless=not self.show_browser,
                            proxy={"server": self.social_proxy},
                        )
                        social_context = await social_browser.new_context(
                            user_agent=(
                                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/123.0.0.0 Safari/537.36"
                            )
                        )
                        social_page = await social_context.new_page()
                try:
                    for file_path in files:
                        await self.process_markdown_file(file_path, page, social_page, client, args)
                finally:
                    if social_context is not None:
                        await social_context.close()
                    if social_browser is not None:
                        await social_browser.close()
                    await context.close()
                    await browser.close()




async def run(args) -> None:
    """Backward-compatible top-level entry point."""
    await MediaProcessor(args).run()

def parse_args():
    parser = argparse.ArgumentParser(
        description="根据 Markdown 中的 IMAGEHOLDER 自动配图配视频；无合适图片时回退即梦文生图。"
    )
    parser.add_argument(
        "target",
        help="目标 Markdown 文件，或包含多篇 Markdown 的目录。",
    )
    parser.add_argument(
        "--suffix",
        default="_with_media",
        help="输出 Markdown 文件后缀（默认 _with_media）。",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="直接覆盖原 Markdown 文件（默认输出新文件）。",
    )
    parser.add_argument(
        "--max-image-candidates",
        type=int,
        default=10,
        help="每个占位最多抓取多少个图片候选（默认 10）。",
    )
    parser.add_argument(
        "--max-video-candidates",
        type=int,
        default=0,
        help="每个占位最多抓取多少个视频候选（默认 0，即默认不抓视频）。",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="调试时显示浏览器窗口（默认无头模式）。",
    )
    parser.add_argument(
        "--image-source",
        choices=["jimeng", "search", "search_then_jimeng", "jimeng_then_search"],
        default="jimeng",
        help=(
            "图片来源策略（默认 jimeng）："
            "jimeng=仅即梦；search=仅搜索；"
            "search_then_jimeng=先搜后即梦；jimeng_then_search=先即梦后搜。"
        ),
    )
    parser.add_argument(
        "--brand-style-mode",
        choices=["article_hash", "fixed", "daily_rotate"],
        default=os.getenv("BRAND_STYLE_MODE", "fixed"),
        help=(
            "品牌风格分配策略："
            "fixed=固定同一种（默认）；"
            "article_hash=按文章名稳定映射；"
            "daily_rotate=按天轮换。"
        ),
    )
    parser.add_argument(
        "--brand-style-name",
        default=os.getenv("BRAND_STYLE_NAME", "dunhuang_mural"),
        help="固定风格名（brand-style-mode=fixed 时生效）。",
    )
    parser.add_argument(
        "--brand-style-cycle",
        default=os.getenv("BRAND_STYLE_CYCLE", DEFAULT_BRAND_STYLE_CYCLE),
        help="可用风格列表，逗号分隔（用于 article_hash/daily_rotate）。",
    )
    parser.add_argument(
        "--social-citation",
        choices=["always", "auto", "off"],
        default="off",
        help="社交平台截图策略：off（默认关闭）、always（每个占位尝试）、auto（按关键词判断）。",
    )
    parser.add_argument(
        "--social-platforms",
        default="reddit,x",
        help="社交平台优先级，逗号分隔（默认 reddit,x）。",
    )
    parser.add_argument(
        "--social-proxy",
        default=(
            os.getenv("PLAYWRIGHT_SOCIAL_PROXY")
            or os.getenv("SOCIAL_PROXY_URL")
            or os.getenv("PLAYWRIGHT_PROXY")
            or ""
        ),
        help=(
            "社交截图代理地址（如 http://127.0.0.1:7890）。"
            "仅 Reddit/X 截图相关浏览流量走该代理。"
        ),
    )
    parser.add_argument(
        "--max-social-candidates",
        type=int,
        default=6,
        help="每个平台最多尝试多少条候选帖子链接（默认 6）。",
    )
    parser.add_argument(
        "--social-shots-per-placeholder",
        type=int,
        default=1,
        help="每个占位最多插入多少张社交截图（默认 1）。",
    )
    parser.add_argument(
        "--jimeng-base-url",
        default=os.getenv("JIMENG_BASE_URL", "https://jimeng.f.rwecho.top"),
        help="即梦 API Base URL（默认 https://jimeng.f.rwecho.top）。",
    )
    parser.add_argument(
        "--jimeng-session-id",
        default=(
            os.getenv("JIMENG_SESSION_ID")
            or os.getenv("JIMENG_TOKEN")
            or os.getenv("JIMENG_AUTH")
            or ""
        ),
        help="即梦 Session ID（Authorization Bearer），默认从环境变量读取。",
    )
    parser.add_argument(
        "--jimeng-model",
        default=os.getenv("JIMENG_MODEL", "jimeng-4.5"),
        help="即梦模型（默认 jimeng-4.5）。",
    )
    parser.add_argument(
        "--jimeng-ratio",
        default=os.getenv("JIMENG_RATIO", "16:9"),
        help="即梦出图比例（默认 16:9）。",
    )
    parser.add_argument(
        "--jimeng-resolution",
        default=os.getenv("JIMENG_RESOLUTION", "2k"),
        help="即梦分辨率（默认 2k）。",
    )
    parser.add_argument(
        "--no-jimeng-fallback",
        action="store_true",
        help="禁用即梦回退（即使检索不到图片也不生成）。",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

