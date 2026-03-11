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
    mongodb_uri: str
    db_name: str
    account_fetch_count: int = 10


@dataclass(frozen=True)
class PdfConfig:
    pdf_dir: str
    playwright_ws_endpoint: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class SchedulerConfig:
    fetch_interval_hours: int = 1


@dataclass(frozen=True)
class AppConfig:
    collector: CollectorConfig
    pdf: PdfConfig
    scheduler: SchedulerConfig


def load_app_config() -> AppConfig:
    data_dir = os.getenv("WECHAT_DATA_DIR", "data")

    collector = CollectorConfig(
        mcp_server_url=os.getenv("MCP_SERVER_URL", "https://wechat-mcp.f.rwecho.top/sse"),
        mongodb_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
        db_name=os.getenv("MONGODB_DB_NAME", "wechat_tracker"),
    )

    pdf = PdfConfig(
        pdf_dir=os.path.join(data_dir, "pdf_exports"),
        playwright_ws_endpoint=os.getenv("PLAYWRIGHT_WS_ENDPOINT", ""),
        enabled=env_bool("PDF_EXPORT_ENABLED", True),
    )

    scheduler = SchedulerConfig(
        fetch_interval_hours=env_int("FETCH_INTERVAL_HOURS", 1),
    )

    return AppConfig(
        collector=collector,
        pdf=pdf,
        scheduler=scheduler,
    )
