import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Загружаем .env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)


class SourceChannel(BaseModel):
    username: str
    title: Optional[str] = None
    original_url: Optional[str] = None
    channel_id: Optional[int] = None


class CollectorConfig(BaseModel):
    poll_interval_sec: int = 180
    channel_delay_sec: float = 2.5
    initial_backfill_limit: int = 1


class ProcessingConfig(BaseModel):
    remove_ads: bool = True
    enrich: bool = True
    max_length: int = 3800
    min_post_interval_sec: int = 300
    timezone: str = "Europe/Moscow"
    active_hours: Dict[str, int] = Field(default_factory=lambda: {"start": 8, "end": 23})


class DedupConfig(BaseModel):
    text_similarity_threshold: float = 0.55
    history_days: int = 14
    topic_lookback_days: int = 3


class EnrichmentConfig(BaseModel):
    add_source_link: bool = False
    signature: str = '\n\n<a href="https://t.me/WrongDevp">WrongDev | AI & Dev</a>'


class PromotionConfig(BaseModel):
    enabled: bool = True
    digest_interval_days: int = 5
    engagement_poll_interval_days: int = 4


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "logs/tg_autoposter.log"


class LLMConfig(BaseModel):
    provider: str = Field(default_factory=lambda: "gemini-cli")
    model: str = Field(default_factory=lambda: "gemini-3.8-flash-high")
    fallback_model: str = Field(default_factory=lambda: "nvidia/nemotron-3-ultra-550b-a55b:free")
    api_key: str = Field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    base_url: str = Field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1"))


class Config(BaseModel):
    admin_ids: List[int] = Field(default_factory=lambda: [int(x.strip()) for x in os.getenv("TELEGRAM_ALLOWED_USERS", "5200292288").split(",") if x.strip()])
    source_channels: List[SourceChannel] = Field(default_factory=list)
    collector: CollectorConfig = Field(default_factory=CollectorConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    enrichment: EnrichmentConfig = Field(default_factory=EnrichmentConfig)
    promotion: PromotionConfig = Field(default_factory=PromotionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)

    # Секреты и параметры из .env
    proxy: str = Field(default_factory=lambda: os.getenv("HTTP_PROXY", "http://127.0.0.1:17890"))
    bot_token: str = Field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    target_channel: str = Field(default_factory=lambda: os.getenv("TARGET_CHANNEL", "@WrongDevp"))
    database_path: str = Field(default_factory=lambda: os.getenv("DATABASE_PATH", "data/tg_autoposter.db"))
    session_name: str = Field(default_factory=lambda: os.getenv("SESSION_NAME", "tg_autoposter_userbot"))
    telegram_api_id: int = Field(default_factory=lambda: int(os.getenv("TELEGRAM_API_ID", "0") or 0))
    telegram_api_hash: str = Field(default_factory=lambda: os.getenv("TELEGRAM_API_HASH", ""))


def load_config(config_path: str = "config.yaml") -> Config:
    path = Path(__file__).parent.parent / config_path
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if "source_channels" in data and isinstance(data["source_channels"], list):
        channels = []
        for item in data["source_channels"]:
            if isinstance(item, str):
                channels.append(SourceChannel(username=item))
            elif isinstance(item, dict):
                channels.append(SourceChannel(**item))
        data["source_channels"] = channels

    return Config(**data)


config = load_config()
