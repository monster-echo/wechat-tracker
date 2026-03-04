import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import quote_plus

SOCIAL_CITATION_HINTS = [
    "争议",
    "热议",
    "风波",
    "爆料",
    "传闻",
    "舆论",
    "网友",
    "讨论",
    "首发",
    "开源",
    "发布",
    "评价",
    "伦理",
    "社区",
]

SOCIAL_NAV_TIMEOUT_MS = int(os.getenv("SOCIAL_NAV_TIMEOUT_MS", "18000"))
SOCIAL_NAV_SETTLE_MS = int(os.getenv("SOCIAL_NAV_SETTLE_MS", "1500"))


def sanitize_filename(filename: str, max_length: int = 96) -> str:
    clean_name = re.sub(r'[\\/*?:"<>|]', "", str(filename).strip())
    clean_name = re.sub(r"\s+", "_", clean_name)
    clean_name = clean_name.strip("._")
    if not clean_name:
        return "asset"
    return clean_name[:max_length]


def normalize_keywords(raw_keywords: str) -> str:
    if not raw_keywords:
        return ""
    parts = [part.strip() for part in re.split(r"[，,、;；]", raw_keywords) if part.strip()]
    return " ".join(parts)


def build_social_query(placeholder: Any) -> str:
    keyword_query = normalize_keywords(getattr(placeholder, "keywords", ""))
    if keyword_query:
        return keyword_query
    compact = " ".join((getattr(placeholder, "query", "") or "").split())
    if compact:
        return " ".join(compact.split()[:10])
    return "AI"


def parse_social_platforms(raw_platforms: str) -> List[str]:
    supported = {"reddit", "x"}
    platforms = []
    for item in (raw_platforms or "").split(","):
        normalized = item.strip().lower()
        if not normalized:
            continue
        if normalized in ("twitter", "twitter.com"):
            normalized = "x"
        if normalized in supported and normalized not in platforms:
            platforms.append(normalized)
    return platforms or ["reddit", "x"]


def should_capture_social(placeholder: Any, args: Any) -> bool:
    if getattr(args, "social_citation", "off") == "off":
        return False
    if getattr(args, "social_citation", "off") == "always":
        return True

    content = f"{getattr(placeholder, 'keywords', '')} {getattr(placeholder, 'description', '')}".lower()
    return any(hint in content for hint in SOCIAL_CITATION_HINTS)


def normalize_social_url(url: str, platform: str) -> str:
    normalized = (url or "").strip()
    if platform == "reddit":
        normalized = re.sub(
            r"^https?://(?:www\.)?reddit\.com",
            "https://old.reddit.com",
            normalized,
            flags=re.IGNORECASE,
        )
    elif platform == "x":
        normalized = re.sub(
            r"^https?://(?:www\.)?(?:x|twitter)\.com",
            "https://fxtwitter.com",
            normalized,
            flags=re.IGNORECASE,
        )
    return normalized


async def goto_with_redirect_tolerance(
    page,
    url: str,
    timeout_ms: int = SOCIAL_NAV_TIMEOUT_MS,
) -> Tuple[bool, str]:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return True, page.url or url
    except Exception as error:
        error_text = str(error)
        if "interrupted by another navigation" in error_text:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                current_url = page.url or ""
                if current_url:
                    return True, current_url
            except Exception:
                pass
            try:
                await page.wait_for_timeout(min(SOCIAL_NAV_SETTLE_MS, 2000))
                current_url = page.url or ""
                if current_url and current_url != "chrome-error://chromewebdata/":
                    return True, current_url
            except Exception:
                pass
        return False, error_text


async def search_social_posts_via_bing(
    page, query: str, platform: str, max_results: int
) -> List[Dict[str, str]]:
    if max_results <= 0:
        return []

    if platform == "reddit":
        platform_query = f"site:reddit.com {query}"
    else:
        platform_query = f"(site:x.com OR site:twitter.com) {query}"

    search_url = f"https://www.bing.com/search?q={quote_plus(platform_query)}"
    ok, note = await goto_with_redirect_tolerance(page, search_url, SOCIAL_NAV_TIMEOUT_MS)
    if not ok:
        raise RuntimeError(note)
    await page.wait_for_timeout(SOCIAL_NAV_SETTLE_MS)

    raw_results = await page.evaluate(
        """
        (limit) => {
          const list = [];
          const seen = new Set();
          for (const anchor of document.querySelectorAll('li.b_algo h2 a, .b_algo a')) {
            const href = anchor.href || '';
            const title = (anchor.textContent || '').trim();
            if (!href || !/^https?:\\/\\//.test(href)) continue;
            if (seen.has(href)) continue;
            seen.add(href);
            list.push({ url: href, title });
            if (list.length >= limit * 3) break;
          }
          return list;
        }
        """,
        max_results,
    )

    filtered = []
    seen = set()
    for item in raw_results or []:
        url = item.get("url", "")
        title = item.get("title", "")
        if not url or url in seen:
            continue
        lowered = url.lower()
        if platform == "reddit":
            if "reddit.com" not in lowered:
                continue
            if "/comments/" not in lowered and "/r/" not in lowered:
                continue
        else:
            if all(token not in lowered for token in ["x.com/", "twitter.com/"]):
                continue
            if "/status/" not in lowered and "/i/web/status/" not in lowered:
                if len(filtered) >= 2:
                    continue
        seen.add(url)
        filtered.append({"url": url, "title": title, "platform": platform})
        if len(filtered) >= max_results:
            break
    return filtered


async def search_reddit_posts_direct(page, query: str, max_results: int) -> List[Dict[str, str]]:
    if max_results <= 0:
        return []
    search_url = f"https://www.reddit.com/search/?q={quote_plus(query)}&sort=relevance&t=year"
    ok, note = await goto_with_redirect_tolerance(page, search_url, SOCIAL_NAV_TIMEOUT_MS)
    if not ok:
        raise RuntimeError(note)
    await page.wait_for_timeout(SOCIAL_NAV_SETTLE_MS)
    results = await page.evaluate(
        """
        (limit) => {
          const items = [];
          const seen = new Set();
          const pushItem = (url, title) => {
            if (!url || !/^https?:\\/\\//.test(url)) return;
            if (!/reddit\\.com\\/.+\\/comments\\//i.test(url)) return;
            if (seen.has(url)) return;
            seen.add(url);
            items.push({ url, title: (title || '').trim(), platform: 'reddit' });
          };

          for (const a of document.querySelectorAll('a[href*=\"/comments/\"]')) {
            const href = a.href || '';
            const title = a.textContent || a.getAttribute('title') || '';
            pushItem(href, title);
            if (items.length >= limit) break;
          }
          return items.slice(0, limit);
        }
        """,
        max_results,
    )
    return results or []


async def capture_social_post_screenshot(
    page, url: str, platform: str, save_path: Path
) -> Tuple[bool, str, str]:
    capture_url = normalize_social_url(url, platform)
    ok, note = await goto_with_redirect_tolerance(page, capture_url, SOCIAL_NAV_TIMEOUT_MS)
    if not ok:
        return False, capture_url, note
    await page.wait_for_timeout(SOCIAL_NAV_SETTLE_MS + 600)

    try:
        await page.evaluate(
            """
            () => {
              for (const node of document.querySelectorAll('[aria-label="Close"], button[aria-label="Close"], .cookie-consent, .consent-banner')) {
                if (node && node.click) {
                  try { node.click(); } catch (_) {}
                }
              }
              for (const fixed of document.querySelectorAll('div,section,aside')) {
                const style = window.getComputedStyle(fixed);
                if (style && style.position === 'fixed' && fixed.clientHeight > window.innerHeight * 0.5) {
                  fixed.style.display = 'none';
                }
              }
            }
            """
        )
    except Exception:
        pass

    selector_candidates = (
        ["div.thing", "article", "shreddit-post", "[data-testid='post-container']"]
        if platform == "reddit"
        else ["article", "main article", "div[data-testid='tweet']"]
    )

    for selector in selector_candidates:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            if count <= 0:
                continue
            await locator.nth(0).screenshot(path=str(save_path))
            return True, capture_url, selector
        except Exception:
            continue

    try:
        await page.screenshot(path=str(save_path), full_page=False)
        return True, capture_url, "viewport"
    except Exception as error:
        return False, capture_url, str(error)


async def capture_platform_search_results_screenshot(
    page, query: str, platform: str, save_path: Path
) -> Tuple[bool, str, str]:
    if platform == "reddit":
        platform_query = f"site:reddit.com {query}"
    else:
        platform_query = f"(site:x.com OR site:twitter.com) {query}"

    search_url = f"https://www.bing.com/search?q={quote_plus(platform_query)}"
    ok, note = await goto_with_redirect_tolerance(page, search_url, SOCIAL_NAV_TIMEOUT_MS)
    if not ok:
        return False, search_url, note
    await page.wait_for_timeout(SOCIAL_NAV_SETTLE_MS)

    try:
        locator = page.locator("#b_results")
        if await locator.count() > 0:
            await locator.nth(0).screenshot(path=str(save_path))
            return True, search_url, "bing-results"
    except Exception:
        pass

    try:
        await page.screenshot(path=str(save_path), full_page=False)
        return True, search_url, "viewport"
    except Exception as error:
        return False, search_url, str(error)


async def collect_social_screenshots(
    page,
    placeholder: Any,
    args: Any,
    assets_dir: Path,
    file_stem: str,
    output_parent: Path,
    logger: Any,
) -> List[Dict[str, str]]:
    if not should_capture_social(placeholder, args):
        return []

    shot_limit = max(getattr(args, "social_shots_per_placeholder", 0), 0)
    if shot_limit == 0:
        return []

    platforms = parse_social_platforms(getattr(args, "social_platforms", "reddit,x"))
    social_query = build_social_query(placeholder)
    logger.info(
        "占位 #%s：社交截图检索词 -> %s",
        getattr(placeholder, "index", "?"),
        social_query,
    )
    screenshots = []

    for platform in platforms:
        if len(screenshots) >= shot_limit:
            break

        platform_label = "Reddit" if platform == "reddit" else "X"
        captured_before = len(screenshots)
        candidates = []
        if platform == "reddit":
            try:
                candidates = await search_reddit_posts_direct(
                    page, social_query, getattr(args, "max_social_candidates", 0)
                )
            except Exception as error:
                logger.warning(
                    "占位 #%s：reddit 直接检索失败：%s",
                    getattr(placeholder, "index", "?"),
                    error,
                )
                candidates = []

        if not candidates:
            try:
                candidates = await search_social_posts_via_bing(
                    page,
                    social_query,
                    platform,
                    getattr(args, "max_social_candidates", 0),
                )
            except Exception as error:
                logger.warning(
                    "占位 #%s：%s 候选检索失败：%s",
                    getattr(placeholder, "index", "?"),
                    platform,
                    error,
                )
                candidates = []

        logger.info(
            "占位 #%s：%s 候选 %s 条",
            getattr(placeholder, "index", "?"),
            platform_label,
            len(candidates),
        )
        for index, candidate in enumerate(candidates, 1):
            if len(screenshots) >= shot_limit:
                break

            shot_name = (
                f"{sanitize_filename(file_stem)}_ph{getattr(placeholder, 'index', 0):02d}"
                f"_{platform}_{index:02d}.png"
            )
            shot_path = assets_dir / shot_name
            ok, capture_url, capture_note = await capture_social_post_screenshot(
                page, candidate["url"], platform, shot_path
            )
            if not ok:
                logger.warning(
                    "占位 #%s：%s 截图失败：%s",
                    getattr(placeholder, "index", "?"),
                    platform,
                    capture_note,
                )
                continue

            screenshots.append(
                {
                    "platform": platform,
                    "source_url": candidate["url"],
                    "capture_url": capture_url,
                    "title": candidate.get("title", ""),
                    "screenshot_path": str(shot_path),
                    "screenshot_ref": os.path.relpath(shot_path, output_parent),
                    "capture_note": capture_note,
                }
            )

        if len(screenshots) > captured_before:
            continue

        if len(screenshots) < shot_limit:
            fallback_name = (
                f"{sanitize_filename(file_stem)}_ph{getattr(placeholder, 'index', 0):02d}"
                f"_{platform}_bing_results.png"
            )
            fallback_path = assets_dir / fallback_name
            ok, capture_url, capture_note = await capture_platform_search_results_screenshot(
                page,
                social_query,
                platform,
                fallback_path,
            )
            if ok:
                logger.info(
                    "占位 #%s：%s 使用 Bing 结果页截图兜底",
                    getattr(placeholder, "index", "?"),
                    platform_label,
                )
                screenshots.append(
                    {
                        "platform": platform,
                        "source_url": capture_url,
                        "capture_url": capture_url,
                        "title": f"{platform_label} 搜索结果（Bing）",
                        "screenshot_path": str(fallback_path),
                        "screenshot_ref": os.path.relpath(fallback_path, output_parent),
                        "capture_note": capture_note,
                    }
                )
                continue

            logger.warning(
                "占位 #%s：%s Bing 结果页截图失败：%s",
                getattr(placeholder, "index", "?"),
                platform_label,
                capture_note,
            )

    return screenshots
