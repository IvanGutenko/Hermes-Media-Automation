import sqlite3
import json
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import threading
import logging

from .config import config

logger = logging.getLogger(__name__)


class Storage:
    """SQLite хранилище для истории постов, дедупликации, состояния сбора и метрик"""

    def __init__(self, db_path: str = None):
        raw_path = db_path or config.database_path
        if not Path(raw_path).is_absolute():
            self.db_path = str(Path(__file__).parent.parent / raw_path)
        else:
            self.db_path = raw_path
            
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL;")
        return self._local.conn

    @contextmanager
    def transaction(self):
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"DB Transaction failed: {e}")
            raise

    def _init_db(self):
        with self.transaction() as conn:
            # 1. Создаем базовые таблицы
            conn.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_channel TEXT NOT NULL,
                    source_msg_id INTEGER NOT NULL,
                    source_date TEXT,
                    original_text TEXT,
                    processed_text TEXT,
                    text_hash TEXT,
                    embedding BLOB,
                    posted_to_target BOOLEAN DEFAULT FALSE,
                    target_msg_id INTEGER,
                    posted_date TEXT,
                    metadata TEXT
                );
            """)

            # 2. Добавляем недостающие колонки при необходимости
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(posts)")
            existing_cols = {row["name"] for row in cur.fetchall()}
            
            for col, col_def in [
                ("status", "TEXT DEFAULT 'collected'"),
                ("key_topic", "TEXT"),
                ("title", "TEXT"),
                ("media_urls", "TEXT"),
                ("skip_reason", "TEXT"),
            ]:
                if col not in existing_cols:
                    try:
                        conn.execute(f"ALTER TABLE posts ADD COLUMN {col} {col_def};")
                    except Exception as ex:
                        logger.warning(f"Column migration notice for {col}: {ex}")

            # 3. Индексы posts
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_source ON posts(source_channel, source_msg_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_hash ON posts(text_hash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_date ON posts(posted_date);")

            # 4. Вспомогательные таблицы
            conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_state (
                    username TEXT PRIMARY KEY,
                    last_seen_msg_id INTEGER DEFAULT 0,
                    last_scraped_at TEXT
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS promo_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    posted_date TEXT NOT NULL,
                    target_msg_id INTEGER
                );
            """)
            cur.execute("PRAGMA table_info(promo_posts)")
            existing_promo_cols = {row["name"] for row in cur.fetchall()}
            if "target_msg_id" not in existing_promo_cols:
                try:
                    conn.execute("ALTER TABLE promo_posts ADD COLUMN target_msg_id INTEGER;")
                except Exception:
                    pass

            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    posts_collected INTEGER DEFAULT 0,
                    posts_published INTEGER DEFAULT 0,
                    posts_skipped_dup INTEGER DEFAULT 0,
                    posts_skipped_ads INTEGER DEFAULT 0,
                    avg_views INTEGER DEFAULT 0,
                    avg_forwards INTEGER DEFAULT 0
                );
            """)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_metrics_date ON metrics(date);")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS post_analytics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER UNIQUE,
                    target_msg_id INTEGER NOT NULL,
                    views INTEGER DEFAULT 0,
                    reactions_count INTEGER DEFAULT 0,
                    reactions_detail TEXT,
                    comments_count INTEGER DEFAULT 0,
                    engagement_rate REAL DEFAULT 0.0,
                    popularity_score REAL DEFAULT 0.0,
                    verdict TEXT,
                    recommendation TEXT,
                    updated_at TEXT,
                    FOREIGN KEY(post_id) REFERENCES posts(id)
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_post_analytics_msg ON post_analytics(target_msg_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_post_analytics_verdict ON post_analytics(verdict);")


    # --- Posts ---
    def get_post_by_source(self, source_channel: str, source_msg_id: int) -> Optional[Dict[str, Any]]:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM posts WHERE source_channel=? AND source_msg_id=?",
                (source_channel, source_msg_id),
            ).fetchone()
            return dict(row) if row else None

    def record_collected_post(
        self,
        source_channel: str,
        source_msg_id: int,
        source_date: str,
        original_text: str,
        media_urls: List[str] = None,
        text_hash: str = None
    ) -> int:
        if not text_hash and original_text:
            text_hash = hashlib.sha256(" ".join(original_text.lower().split()).encode()).hexdigest()

        with self.transaction() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO posts 
                   (source_channel, source_msg_id, source_date, original_text, media_urls, text_hash, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'collected')""",
                (
                    source_channel,
                    source_msg_id,
                    source_date,
                    original_text,
                    json.dumps(media_urls) if media_urls else None,
                    text_hash,
                ),
            )
            if cur.lastrowid:
                return cur.lastrowid
            
            row = conn.execute(
                "SELECT id FROM posts WHERE source_channel=? AND source_msg_id=?",
                (source_channel, source_msg_id)
            ).fetchone()
            return row["id"] if row else 0

    def mark_policy_skipped(self, post_id):
        with self.transaction() as conn:
            conn.execute("UPDATE posts SET status='skipped_policy', skip_reason='owner_topic_policy' WHERE id=?", (post_id,))

    def mark_skipped(self, post_id: int, reason: str, is_ad: bool = False):
        status = "skipped_ads" if is_ad else "skipped_dup"
        with self.transaction() as conn:
            conn.execute(
                "UPDATE posts SET status=?, skip_reason=? WHERE id=?",
                (status, reason, post_id),
            )

    def mark_posted(
        self,
        post_id: int,
        target_msg_id: int,
        processed_text: str,
        title: str = None,
        key_topic: str = None,
        metadata: dict = None
    ):
        now = datetime.now().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """UPDATE posts SET 
                   status='posted',
                   posted_to_target=TRUE,
                   target_msg_id=?,
                   processed_text=?,
                   title=?,
                   key_topic=?,
                   posted_date=?,
                   metadata=?
                   WHERE id=?""",
                (
                    target_msg_id,
                    processed_text,
                    title,
                    key_topic,
                    now,
                    json.dumps(metadata) if metadata else None,
                    post_id,
                ),
            )

    def mark_pending(
        self,
        post_id: int,
        processed_text: str,
        title: str = None,
        key_topic: str = None,
        metadata: dict = None
    ):
        with self.transaction() as conn:
            conn.execute(
                """UPDATE posts SET 
                   status='pending',
                   processed_text=?,
                   title=?,
                   key_topic=?,
                   metadata=?
                   WHERE id=?""",
                (
                    processed_text,
                    title,
                    key_topic,
                    json.dumps(metadata) if metadata else None,
                    post_id,
                ),
            )

    def get_unprocessed_posts(self, limit: int = 10):
        with self.transaction() as conn:
            rows = conn.execute(
                """SELECT id, source_channel, source_msg_id, source_date, original_text, media_urls, text_hash
                   FROM posts 
                   WHERE status='collected' AND posted_to_target=FALSE
                   ORDER BY id ASC LIMIT ?""",
                (limit,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if d.get("media_urls"):
                    try:
                        d["media_urls"] = json.loads(d["media_urls"])
                    except Exception:
                        d["media_urls"] = []
                else:
                    d["media_urls"] = []
                result.append(d)
            return result

    def get_pending_posts(self, limit: int = 10):
        with self.transaction() as conn:
            rows = conn.execute(
                """SELECT id, source_channel, source_msg_id, source_date, original_text, 
                          processed_text, title, key_topic, media_urls, text_hash, metadata
                   FROM posts 
                   WHERE status='pending' AND posted_to_target=FALSE
                   ORDER BY id ASC LIMIT ?""",
                (limit,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if d.get("media_urls"):
                    try:
                        d["media_urls"] = json.loads(d["media_urls"])
                    except Exception:
                        d["media_urls"] = []
                else:
                    d["media_urls"] = []
                if d.get("metadata"):
                    try:
                        d["metadata"] = json.loads(d["metadata"])
                    except Exception:
                        d["metadata"] = {}
                else:
                    d["metadata"] = {}
                result.append(d)
            return result

    def get_queue_count(self):
        with self.transaction() as conn:
            row = conn.execute("""
                SELECT 
                    SUM(CASE WHEN status='collected' AND posted_to_target=FALSE THEN 1 ELSE 0 END) as collected,
                    SUM(CASE WHEN status='pending' AND posted_to_target=FALSE THEN 1 ELSE 0 END) as pending
                FROM posts
            """).fetchone()
            return {
                "collected": row["collected"] if row and row["collected"] is not None else 0,
                "pending": row["pending"] if row and row["pending"] is not None else 0,
            }

    # --- Dedup queries ---
    def get_recent_hashes(self, days: int = 14) -> List[str]:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT text_hash FROM posts WHERE posted_to_target=TRUE AND text_hash IS NOT NULL AND source_date >= ?",
                (cutoff,),
            ).fetchall()
            return [r["text_hash"] for r in rows if r["text_hash"]]

    def get_recent_published_posts(self, days: int = 7, limit: int = 100) -> List[Dict[str, Any]]:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self.transaction() as conn:
            rows = conn.execute(
                """SELECT id, title, key_topic, original_text, processed_text, posted_date 
                   FROM posts 
                   WHERE posted_to_target=TRUE AND posted_date >= ?
                   ORDER BY posted_date DESC LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Channel state ---
    def get_channel_last_seen_id(self, username: str) -> int:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT last_seen_msg_id FROM channel_state WHERE username=?",
                (username,)
            ).fetchone()
            return row["last_seen_msg_id"] if row else 0

    def set_channel_last_seen_id(self, username: str, msg_id: int):
        now = datetime.now().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO channel_state (username, last_seen_msg_id, last_scraped_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(username) DO UPDATE SET 
                   last_seen_msg_id=MAX(last_seen_msg_id, excluded.last_seen_msg_id),
                   last_scraped_at=excluded.last_scraped_at""",
                (username, msg_id, now),
            )

    # --- Promo posts ---
    def record_promo_post(self, promo_type: str, content: str, target_msg_id: int):
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO promo_posts (type, content, posted_date, target_msg_id) VALUES (?, ?, ?, ?)",
                (promo_type, content, datetime.now().isoformat(), target_msg_id),
            )

    def get_last_promo_date(self, promo_type: str) -> Optional[datetime]:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT posted_date FROM promo_posts WHERE type=? ORDER BY posted_date DESC LIMIT 1",
                (promo_type,),
            ).fetchone()
            if row and row["posted_date"]:
                return datetime.fromisoformat(row["posted_date"])
            return None

    # --- Metrics ---
    def increment_metric(self, field: str, value: int = 1):
        date_str = datetime.now().strftime("%Y-%m-%d")
        with self.transaction() as conn:
            row = conn.execute("SELECT id FROM metrics WHERE date=?", (date_str,)).fetchone()
            if row:
                conn.execute(
                    f"UPDATE metrics SET {field} = {field} + ? WHERE date=?",
                    (value, date_str)
                )
            else:
                conn.execute(
                    f"INSERT INTO metrics (date, {field}) VALUES (?, ?)",
                    (date_str, value)
                )



    # --- Post Analytics ---
    def upsert_post_analytics(
        self,
        post_id: int,
        target_msg_id: int,
        views: int,
        reactions_count: int,
        reactions_detail: str,
        comments_count: int,
        engagement_rate: float,
        popularity_score: float,
        verdict: str,
        recommendation: str,
    ):
        now = datetime.now().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO post_analytics 
                   (post_id, target_msg_id, views, reactions_count, reactions_detail, comments_count, engagement_rate, popularity_score, verdict, recommendation, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(post_id) DO UPDATE SET
                   target_msg_id=excluded.target_msg_id,
                   views=excluded.views,
                   reactions_count=excluded.reactions_count,
                   reactions_detail=excluded.reactions_detail,
                   comments_count=excluded.comments_count,
                   engagement_rate=excluded.engagement_rate,
                   popularity_score=excluded.popularity_score,
                   verdict=excluded.verdict,
                   recommendation=excluded.recommendation,
                   updated_at=excluded.updated_at""",
                (
                    post_id,
                    target_msg_id,
                    views,
                    reactions_count,
                    reactions_detail,
                    comments_count,
                    engagement_rate,
                    popularity_score,
                    verdict,
                    recommendation,
                    now,
                ),
            )

    def get_post_analytics(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.transaction() as conn:
            rows = conn.execute(
                """SELECT pa.*, p.title, p.key_topic, p.posted_date
                   FROM post_analytics pa
                   JOIN posts p ON pa.post_id = p.id
                   ORDER BY pa.views DESC, pa.reactions_count DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

storage = Storage()
