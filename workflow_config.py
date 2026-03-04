import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class CollectorConfig:
    mcp_server_url: str
    accounts_file: str
    history_file: str
    daily_folder: str
    account_fetch_count: int = 10


@dataclass(frozen=True)
class PdfConfig:
    pdf_dir: str
    playwright_ws_endpoint: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class TopicGenerationConfig:
    topic_mode: str = "ai"
    source: str = "auto"
    max_items: int = 12
    max_topics: int = 5
    max_candidates: int = 250
    min_draft_topics: int = 5
    max_draft_topics: int = 5
    draft_target_words: int = 1600
    min_imageholders: int = 3
    model: str = "deepseek-chat"
    api_base: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    draft_model: str = ""
    no_fallback: bool = False
    draft_dir: str = ""


@dataclass(frozen=True)
class MediaGenerationConfig:
    enabled: bool = True
    image_source: str = "jimeng"
    brand_style_mode: str = "fixed"
    brand_style_name: str = "dunhuang_mural"
    brand_style_cycle: str = (
        "oracle_bone,dunhuang_mural,steampunk,"
        "ink_illustration,guochao_poster,paper_cut_collage,"
        "woodcut_print,editorial_illustration,flat_illustration,"
        "isometric_infographic,retro_scifi_comic"
    )
    output_suffix: str = "_media_jimeng_auto"
    in_place: bool = False
    show_browser: bool = False
    max_image_candidates: int = 10
    max_video_candidates: int = 0
    social_citation: str = "off"
    social_platforms: str = "reddit,x"
    social_proxy: str = ""
    max_social_candidates: int = 6
    social_shots_per_placeholder: int = 1
    jimeng_base_url: str = "https://jimeng.f.rwecho.top"
    jimeng_session_id: str = ""
    jimeng_model: str = "jimeng-4.5"
    jimeng_ratio: str = "16:9"
    jimeng_resolution: str = "2k"
    no_jimeng_fallback: bool = False


@dataclass(frozen=True)
class SchedulerConfig:
    fetch_interval_hours: int = 1


@dataclass(frozen=True)
class AppConfig:
    collector: CollectorConfig
    pdf: PdfConfig
    topic_generation: TopicGenerationConfig
    media_generation: MediaGenerationConfig
    scheduler: SchedulerConfig


def load_app_config() -> AppConfig:
    data_dir = os.getenv("WECHAT_DATA_DIR", "data")

    collector = CollectorConfig(
        mcp_server_url=os.getenv("MCP_SERVER_URL", "https://wechat-mcp.f.rwecho.top/sse"),
        accounts_file=os.path.join(data_dir, "accounts.txt"),
        history_file=os.path.join(data_dir, "articles_history.json"),
        daily_folder=os.path.join(data_dir, "daily_reports"),
    )

    pdf = PdfConfig(
        pdf_dir=os.path.join(data_dir, "pdf_exports"),
        playwright_ws_endpoint=os.getenv("PLAYWRIGHT_WS_ENDPOINT", ""),
        enabled=env_bool("PDF_EXPORT_ENABLED", True),
    )

    model = os.getenv("DEEPSEEK_MODEL") or os.getenv("NEWS_MODEL") or "deepseek-chat"
    api_base = (
        os.getenv("DEEPSEEK_BASE_URL")
        or os.getenv("NEWS_API_BASE")
        or "https://api.deepseek.com/v1"
    )
    api_key = (
        os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("NEWS_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )

    topic_generation = TopicGenerationConfig(
        model=model,
        api_base=api_base,
        api_key=api_key,
    )

    media_generation = MediaGenerationConfig(
        enabled=env_bool("NEWS_ATTACH_MEDIA", True),
        brand_style_name=os.getenv("NEWS_BRAND_STYLE_NAME", "dunhuang_mural"),
        jimeng_base_url=os.getenv("JIMENG_BASE_URL", "https://jimeng.f.rwecho.top"),
        jimeng_session_id=(
            os.getenv("JIMENG_SESSION_ID")
            or os.getenv("JIMENG_TOKEN")
            or os.getenv("JIMENG_AUTH")
            or ""
        ),
        jimeng_model=os.getenv("JIMENG_MODEL", "jimeng-4.5"),
    )

    scheduler = SchedulerConfig(
        fetch_interval_hours=env_int("FETCH_INTERVAL_HOURS", 1),
    )

    return AppConfig(
        collector=collector,
        pdf=pdf,
        topic_generation=topic_generation,
        media_generation=media_generation,
        scheduler=scheduler,
    )
