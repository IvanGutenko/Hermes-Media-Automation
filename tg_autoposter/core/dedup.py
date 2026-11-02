import hashlib
import re
import logging
from typing import List, Dict, Any, Tuple, Set, Optional
from datetime import datetime
from urllib.parse import urlparse

from .config import config
from .storage import storage

logger = logging.getLogger(__name__)


def extract_project_identifiers(text: str) -> Set[str]:
    """Извлекает нормализованные идентификаторы проектов, репозиториев и ссылок из текста."""
    if not text:
        return set()

    urls = re.findall(r"https?://[^\s<>\"'\)]+", text)
    identifiers = set()

    for u in urls:
        u = u.rstrip(".,;!?\"')")
        try:
            parsed = urlparse(u.lower())
        except Exception:
            continue

        netloc = parsed.netloc.replace("www.", "")
        path = parsed.path.rstrip("/")

        # 1. GitHub репозитории: github:owner/repo
        if "github.com" in netloc:
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2:
                identifiers.add(f"github:{parts[0]}/{parts[1]}")
                continue

        # 2. Hugging Face модели/спейсы: hf:owner/model
        if "huggingface.co" in netloc:
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2:
                identifiers.add(f"hf:{parts[0]}/{parts[1]}")
                continue

        # 3. ArXiv статьи: arxiv:id
        if "arxiv.org" in netloc:
            parts = [p for p in path.split("/") if p]
            if parts:
                identifiers.add(f"arxiv:{parts[-1]}")
                continue

        # 4. Целевые домены проектов (игнорируем общие домены и мессенджеры)
        if netloc and netloc not in ("t.me", "telegram.me", "telesco.pe", "google.com", "youtube.com", "youtu.be"):
            clean_path = path[:35] if path else ""
            identifiers.add(f"url:{netloc}{clean_path}")

    return identifiers


class DedupEngine:
    """Многоуровневая дедупликация:
    1. Идентичность репозитория / ссылки на проект (GitHub, HuggingFace, arXiv, сайт проекта)
    2. Точный хеш нормализованного текста
    3. Jaccard-сходство ключевых токенов текста (порог 0.40)
    4. Сходство ключевой темы и заголовков за последние 14 дней
    """

    def __init__(self):
        self.threshold = getattr(config.dedup, "text_similarity_threshold", 0.40)
        self.topic_lookback_days = getattr(config.dedup, "topic_lookback_days", 14)
        self._hash_cache: Optional[Set[str]] = None
        self._recent_posts_cache: Optional[List[Dict[str, Any]]] = None
        self._last_cache_update = 0

    def _normalize_text(self, text: str) -> str:
        return " ".join(text.lower().split())

    def _get_text_hash(self, text: str) -> str:
        return hashlib.sha256(self._normalize_text(text).encode("utf-8")).hexdigest()

    def _extract_tokens(self, text: str) -> Set[str]:
        words = re.findall(r"[a-zа-яё0-9_\-\.]+", text.lower())
        stopwords = {
            "это", "как", "так", "для", "что", "или", "все", "при", "уже", "если",
            "быть", "свой", "тоже", "даже", "через", "после", "только", "теперь",
            "this", "that", "with", "from", "have", "more", "also", "then", "into"
        }
        return {w for w in words if len(w) >= 3 and w not in stopwords}

    def _jaccard_similarity(self, set_a: Set[str], set_b: Set[str]) -> float:
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return float(intersection / union) if union > 0 else 0.0

    def _load_caches(self):
        import time
        now = time.time()
        if self._hash_cache is None or (now - self._last_cache_update) > 45:
            self._hash_cache = set(storage.get_recent_hashes(days=config.dedup.history_days))
            posts = storage.get_recent_published_posts(days=self.topic_lookback_days, limit=150)
            
            # Предварительно рассчитываем идентификаторы проектов и токены
            for p in posts:
                full_text = (p.get("original_text") or "") + " " + (p.get("processed_text") or "")
                p["_project_ids"] = extract_project_identifiers(full_text)
                p["_tokens"] = self._extract_tokens(full_text)
                topic_str = (p.get("key_topic") or "") + " " + (p.get("title") or "")
                p["_topic_tokens"] = self._extract_tokens(topic_str)
                
            self._recent_posts_cache = posts
            self._last_cache_update = now

    def is_duplicate(
        self,
        raw_text: str,
        key_topic: Optional[str] = None,
        title: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Проверяет текст, ссылки на проекты и тему на дубликаты."""
        if not raw_text or len(raw_text.strip()) < 40:
            return False, "too_short"

        self._load_caches()

        # 1. Быстрый точный хеш текста
        text_hash = self._get_text_hash(raw_text)
        if text_hash in self._hash_cache:
            return True, "exact_text_hash"

        # 2. Идентичность репозитория / проекта (самый надёжный фильтр для IT-каналов)
        new_project_ids = extract_project_identifiers(raw_text)
        if new_project_ids and self._recent_posts_cache:
            for past in self._recent_posts_cache:
                past_ids = past.get("_project_ids") or set()
                matched = new_project_ids & past_ids
                if matched:
                    return True, f"duplicate_project_{list(matched)[0]}_with_post_{past.get('id')}"

        # 3. Семантическое сходство токенов текста (порог 0.40)
        new_tokens = self._extract_tokens(raw_text)
        if len(new_tokens) >= 8 and self._recent_posts_cache:
            for past in self._recent_posts_cache:
                past_tokens = past.get("_tokens") or set()
                sim = self._jaccard_similarity(new_tokens, past_tokens)
                if sim >= self.threshold:
                    return True, f"token_similarity_{sim:.2f}_with_post_{past.get('id')}"

        # 4. Сходство темы и заголовка (порог 0.45)
        new_topic_str = (key_topic or "") + " " + (title or "")
        new_topic_tokens = self._extract_tokens(new_topic_str)
        if len(new_topic_tokens) >= 2 and self._recent_posts_cache:
            for past in self._recent_posts_cache:
                past_topic_tokens = past.get("_topic_tokens") or set()
                t_sim = self._jaccard_similarity(new_topic_tokens, past_topic_tokens)
                if t_sim >= 0.45:
                    return True, f"topic_overlap_{t_sim:.2f}_with_post_{past.get('id')}"

        return False, "unique"

    def record_published(self, post_id: int, text_hash: str, key_topic: str = None, title: str = None):
        if self._hash_cache is not None:
            self._hash_cache.add(text_hash)
        # Сбрасываем метку времени, чтобы на следующем круге обновить кэш с новым постом
        self._last_cache_update = 0


dedup = DedupEngine()
