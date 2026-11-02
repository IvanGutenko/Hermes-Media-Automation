import asyncio
import json
import logging
import statistics
import re
from datetime import datetime
from typing import Dict, Any, List, Optional
import httpx
from bs4 import BeautifulSoup

from .config import config
from .storage import storage

logger = logging.getLogger(__name__)


class AnalyticsEngine:
    """Модуль глубокого анализа эффективности публикаций и выработки контент-стратегии.
    Собирает просмотры, реакции и комментарии из канала, рассчитывает ER и Popularity Score,
    выявляет трендовые и провальные темы, дает конкретные рекомендации автору."""

    def __init__(self):
        self.proxy = config.proxy

    async def fetch_live_channel_stats(self, channel_username: Optional[str] = None) -> Dict[int, Dict[str, Any]]:
        """Парсит открытую веб-страницу t.me/s/<channel> через прокси и собирает live-метрики по всем видимым сообщениям."""
        ch = (channel_username or config.target_channel).lstrip("@")
        url = f"https://t.me/s/{ch}"
        stats: Dict[int, Dict[str, Any]] = {}

        try:
            async with httpx.AsyncClient(proxy=self.proxy, timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                })

            if resp.status_code != 200:
                logger.warning(f"Failed to fetch live stats from {url}: HTTP {resp.status_code}")
                return {}

        except Exception as e:
            logger.error(f"Network error fetching stats from {url}: {e}")
            return {}

        soup = BeautifulSoup(resp.text, "html.parser")
        for m in soup.find_all("div", class_="tgme_widget_message"):
            data_post = m.get("data-post")
            if not data_post or "/" not in data_post:
                continue

            try:
                msg_id = int(data_post.split("/")[-1])
            except ValueError:
                continue

            # 1. Просмотры
            views = 0
            views_elem = m.find("span", class_="tgme_widget_message_views")
            if views_elem:
                v_text = views_elem.get_text(strip=True).upper()
                if "K" in v_text:
                    views = int(float(v_text.replace("K", "").replace(",", ".")) * 1000)
                elif "M" in v_text:
                    views = int(float(v_text.replace("M", "").replace(",", ".")) * 1000000)
                else:
                    try:
                        views = int(v_text)
                    except ValueError:
                        views = 0

            # 2. Реакции
            reactions: Dict[str, int] = {}
            for r in m.find_all("span", class_="tgme_reaction"):
                emoji_tag = r.find("b")
                emoji_char = emoji_tag.get_text(strip=True) if emoji_tag else ""
                full_text = r.get_text(strip=True)
                # извлекаем цифру
                digits = re.findall(r"\d+", full_text)
                count = int(digits[0]) if digits else 1
                key = emoji_char or "👍"
                reactions[key] = count

            reactions_count = sum(reactions.values())

            # 3. Комментарии
            comments_elem = m.find("span", class_="tgme_widget_message_comments")
            comments_count = 0
            if comments_elem:
                comm_text = comments_elem.get_text(strip=True)
                digits = re.findall(r"\d+", comm_text)
                if digits:
                    comments_count = int(digits[0])

            stats[msg_id] = {
                "views": views,
                "reactions": reactions,
                "reactions_count": reactions_count,
                "comments_count": comments_count,
            }

        logger.info(f"Collected live metrics for {len(stats)} messages in @{ch}")
        return stats

    async def update_all_posts_analytics(self) -> Dict[str, Any]:
        """Обновляет метрики всех опубликованных постов в БД, пересчитывает бенчмарки и возвращает полный отчет."""
        # 1. Загружаем все опубликованные посты из БД
        with storage.transaction() as conn:
            cur = conn.cursor()
            rows = cur.execute("""
                SELECT id, target_msg_id, title, key_topic, original_text, processed_text, posted_date
                FROM posts
                WHERE posted_to_target = 1 AND target_msg_id IS NOT NULL
                ORDER BY id DESC
            """).fetchall()
            db_posts = [dict(r) for r in rows]

        if not db_posts:
            return {"status": "no_posts", "summary": "Нет опубликованных постов для анализа."}

        # 2. Парсим свежие метрики из Telegram
        live_stats = await self.fetch_live_channel_stats()

        # 3. Объединяем данные
        matched_posts: List[Dict[str, Any]] = []
        for p in db_posts:
            mid = p.get("target_msg_id")
            st = live_stats.get(mid)
            if st:
                p["views"] = st["views"]
                p["reactions"] = st["reactions"]
                p["reactions_count"] = st["reactions_count"]
                p["comments_count"] = st["comments_count"]
            else:
                p["views"] = 0
                p["reactions"] = {}
                p["reactions_count"] = 0
                p["comments_count"] = 0

            # Engagement Rate (% вовлеченности от охвата)
            views = p["views"]
            total_eng = p["reactions_count"] + p["comments_count"]
            p["engagement_rate"] = round((total_eng / max(views, 1)) * 100, 2)
            matched_posts.append(p)

        # 4. Расчет средних и медианных значений по каналу
        views_list = [p["views"] for p in matched_posts if p["views"] > 0] or [1]
        median_views = statistics.median(views_list)
        mean_views = round(statistics.mean(views_list), 1)
        max_views = max(views_list)
        total_reactions = sum(p["reactions_count"] for p in matched_posts)

        # 5. Классификация каждого поста (Вердикт + Рекомендация)
        high_threshold = max(median_views * 1.2, median_views + 2)
        low_threshold = max(median_views * 0.75, 1)

        for p in matched_posts:
            views = p["views"]
            reax = p["reactions_count"]
            er = p["engagement_rate"]

            # Скоринг популярности (0..100)
            norm_views = min(50.0, (views / max(median_views, 1)) * 30.0)
            norm_reax = min(40.0, reax * 20.0)
            norm_er = min(10.0, er * 1.0)
            score = round(norm_views + norm_reax + norm_er, 1)
            p["popularity_score"] = score

            # Определение вердикта
            if views >= high_threshold or reax >= 1 or er >= 8.0:
                verdict = "scale"
                verdict_title = "🔥 Залетает (Масштабировать)"
                rec = (
                    "Тема зацепила аудиторию выше среднего. "
                    "Рекомендуется: 1) Написать пост-продолжение с глубоким разбором; "
                    "2) Сделать короткое видео (Shorts/Reels) на эту тему; "
                    "3) Использовать аналогичный хук и структуру подачи."
                )
            elif views <= low_threshold and reax == 0:
                verdict = "iterate"
                verdict_title = "⚠️ Слабо (Пробовать другое)"
                rec = (
                    "Низкий охват и нулевой отклик. "
                    "Рекомендуется: 1) Сменить угол подачи (вопрос к разработчикам или конкретная выгода вместо сухого анонса); "
                    "2) Добавить практический пример/код/скриншот; "
                    "3) Если следующая итерация темы не наберет отклика — временно убрать направление из сетки."
                )
            else:
                verdict = "normal"
                verdict_title = "👌 В норме (Поддерживать)"
                rec = (
                    "Стабильный интерес аудитории. "
                    "Рекомендуется: сохранять в регулярной сетке публикаций, ротировать с другими темами."
                )

            p["verdict"] = verdict
            p["verdict_title"] = verdict_title
            p["recommendation"] = rec

            # Сохраняем в таблицу post_analytics в SQLite
            storage.upsert_post_analytics(
                post_id=p["id"],
                target_msg_id=p["target_msg_id"],
                views=views,
                reactions_count=reax,
                reactions_detail=json.dumps(p["reactions"], ensure_ascii=False),
                comments_count=p["comments_count"],
                engagement_rate=er,
                popularity_score=score,
                verdict=verdict,
                recommendation=rec,
            )

        # 6. Анализ по тематическим кластерам (key_topic)
        topic_groups: Dict[str, List[Dict[str, Any]]] = {}
        for p in matched_posts:
            topic = p.get("key_topic") or "Общее"
            topic_groups.setdefault(topic, []).append(p)

        topic_stats = []
        for topic, posts in topic_groups.items():
            t_views = [p["views"] for p in posts]
            t_reax = sum(p["reactions_count"] for p in posts)
            avg_v = round(statistics.mean(t_views), 1)
            scale_cnt = sum(1 for p in posts if p["verdict"] == "scale")
            iterate_cnt = sum(1 for p in posts if p["verdict"] == "iterate")
            
            if scale_cnt > iterate_cnt or t_reax > 0:
                t_verdict = "Масштабировать"
            elif iterate_cnt > scale_cnt and avg_v < median_views:
                t_verdict = "Тестировать другую подачу"
            else:
                t_verdict = "Стабильно"

            topic_stats.append({
                "topic": topic,
                "count": len(posts),
                "avg_views": avg_v,
                "reactions": t_reax,
                "verdict": t_verdict,
            })

        topic_stats.sort(key=lambda x: (x["reactions"], x["avg_views"]), reverse=True)

        return {
            "status": "ok",
            "posts_tracked": len(matched_posts),
            "median_views": median_views,
            "mean_views": mean_views,
            "max_views": max_views,
            "total_reactions": total_reactions,
            "posts": matched_posts,
            "topic_stats": topic_stats,
        }

    async def generate_telegram_report(self) -> str:
        """Show measured multi-platform feedback, without invented strategic claims."""
        import sys
        sys.path.insert(0, '/home/gut/hermes-workspace/video_factory/scripts')
        from content_feedback import format_report
        return format_report()


    async def run_periodic(self, interval_sec: int = 7200):
        """Фоновый цикл периодического обновления статистики постов и тем каждые 2 часа."""
        logger.info("Analytics background updater running")
        while True:
            try:
                await self.update_all_posts_analytics()
            except Exception as e:
                logger.error(f"Periodic analytics update error: {e}")
            await asyncio.sleep(interval_sec)


analytics = AnalyticsEngine()
