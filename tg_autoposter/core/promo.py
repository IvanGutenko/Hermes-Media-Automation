import asyncio
import logging
import random
import re
from datetime import datetime, timedelta
from typing import List, Optional

from .config import config
from .storage import storage
from .bot_poster import bot_poster
from .dedup import extract_project_identifiers

logger = logging.getLogger(__name__)


class PromoEngine:
    """Движок органического и бесплатного роста Telegram-канала:
    1. Автоматические периодические дайджесты лучших AI-новинок
    2. Интерактивные опросы по технологиям
    3. Вирусные форматы шпаргалок и подборок
    """

    def __init__(self):
        self.digest_interval_days = config.promotion.digest_interval_days
        self.poll_interval_days = config.promotion.engagement_poll_interval_days

    async def run_periodic(self):
        """Фоновый цикл проверки и публикации виральных материалов."""
        if not config.promotion.enabled:
            return

        logger.info("Promo engine running in background")
        while True:
            try:
                await self._check_and_run_digest()
                await self._check_and_run_poll()
            except Exception as e:
                logger.error(f"Promo engine error: {e}")

            # Проверяем каждые 2 часа
            await asyncio.sleep(7200)

    async def _check_and_run_digest(self):
        """Проверяет необходимость выпуска дайджеста."""
        last_date = storage.get_last_promo_date("weekly_digest")
        should_run = False

        if not last_date:
            # Если дайджест еще ни разу не выходил, ждем пока наберется минимум 5 опубликованных постов
            recent = storage.get_recent_published_posts(days=14, limit=10)
            if len(recent) >= 5:
                should_run = True
        elif datetime.now() - last_date >= timedelta(days=self.digest_interval_days):
            should_run = True

        if should_run and bot_poster.is_within_active_hours():
            await self.post_digest()

    async def _check_and_run_poll(self):
        """Проверяет необходимость выпуска вовлекающего опроса."""
        last_date = storage.get_last_promo_date("poll")
        should_run = False

        if not last_date:
            should_run = False
        elif datetime.now() - last_date >= timedelta(days=self.poll_interval_days):
            should_run = True

        if should_run and bot_poster.is_within_active_hours():
            await self.post_engagement_poll()

    def _extract_post_summary(self, text: str) -> str:
        """Извлекает первое содержательное предложение из тела поста, пропуская заголовок."""
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        if not paragraphs:
            return ""

        # Первый абзац — это заголовок <b>...</b>, берем последующие
        body_part = ""
        for p in paragraphs[1:]:
            clean = re.sub(r"<[^>]+>", "", p).strip()
            # Пропускаем ссылки, пункты меню и подпись
            if (
                clean
                and not clean.startswith("👉")
                and not clean.startswith("http")
                and not clean.startswith("•")
                and "@WrongDevp" not in clean
                and "WrongDev" not in clean
            ):
                body_part = clean
                break

        if not body_part and len(paragraphs) > 0:
            body_part = re.sub(r"<[^>]+>", "", paragraphs[-1])

        # Выделяем первое законченное предложение
        sentences = re.split(r"(?<=[.!?])\s+", body_part)
        first_sentence = sentences[0] if sentences else body_part
        if len(first_sentence) > 130:
            first_sentence = first_sentence[:127] + "…"
        return first_sentence

    async def post_digest(self) -> Optional[int]:
        """Генерирует и публикует дайджест лучших инструментов за последние дни без дубликатов."""
        recent = storage.get_recent_published_posts(days=14, limit=30)
        if len(recent) < 3:
            logger.info("Not enough posts for a digest yet")
            return None

        # Дедупликация кандидатов по проектам и темам
        seen_projects = set()
        seen_topics = set()
        seen_titles = set()
        unique_posts = []

        for p in recent:
            full_text = (p.get("original_text") or "") + " " + (p.get("processed_text") or "")
            proj_ids = extract_project_identifiers(full_text)
            if proj_ids and any(pid in seen_projects for pid in proj_ids):
                continue

            topic = (p.get("key_topic") or "").lower().strip()
            if topic and topic in seen_topics:
                continue

            title_clean = (p.get("title") or "").lower().strip()
            if title_clean and title_clean in seen_titles:
                continue

            if proj_ids:
                seen_projects.update(proj_ids)
            if topic:
                seen_topics.add(topic)
            if title_clean:
                seen_titles.add(title_clean)

            unique_posts.append(p)
            if len(unique_posts) >= 5:
                break

        if len(unique_posts) < 3:
            logger.info("Not enough unique posts for a digest after deduplication")
            return None

        lines = [
            "🔥 <b>Дайджест AI & Dev: главное за последнее время</b>",
            "<i>Подборка самых полезных инструментов и релизов:</i>\n"
        ]

        target_ch_clean = config.target_channel.lstrip("@")
        for i, p in enumerate(unique_posts, 1):
            title = p.get("title") or p.get("key_topic") or "Инструмент недели"
            target_msg_id = p.get("target_msg_id")

            if target_msg_id:
                link = f"https://t.me/{target_ch_clean}/{target_msg_id}"
                lines.append(f"<b>{i}. <a href=\"{link}\">{title}</a></b>")
            else:
                lines.append(f"<b>{i}. {title}</b>")

            summary = self._extract_post_summary(p.get("processed_text", ""))
            if summary:
                lines.append(f"• {summary}\n")
            else:
                lines.append("")

        lines.append("📌 <i>Сохраняй подборку и делись с коллегами.</i>")
        lines.append('\n<a href="https://t.me/WrongDevp">WrongDev | AI & Dev</a>')

        digest_html = "\n".join(lines).strip()
        msg_id = await bot_poster.post_article(digest_html, title="Дайджест AI-инструментов")
        if msg_id:
            storage.record_promo_post("weekly_digest", digest_html, msg_id)
            logger.info(f"Published promo digest: msg_id={msg_id}")
            return msg_id
        return None

    async def post_engagement_poll(self):
        """Публикует интерактивный опрос в канале."""
        if not bot_poster.bot:
            await bot_poster.init()

        poll_ideas = [
            {
                "question": "Какой AI-инструмент для кода сейчас основной у вас?",
                "options": ["Cursor", "Claude Code / CLI", "GitHub Copilot", "Aider / Continue", "Пишу код руками"],
            },
            {
                "question": "Где чаще запускаете модели?",
                "options": ["Только облачные API (Groq, OpenRouter)", "Локально на GPU (Ollama, vLLM)", "В браузере (ChatGPT / Claude)", "Гибридно под разные задачи"],
            },
            {
                "question": "Какой формат постов вам наиболее полезен?",
                "options": ["Разборы новых инструментов", "Гайды по вайб-кодингу", "Бесплатные AI-раздачи и API", "Квантование и локальный запуск"],
            }
        ]

        poll_data = random.choice(poll_ideas)
        try:
            msg = await bot_poster.bot.send_poll(
                chat_id=config.target_channel,
                question=poll_data["question"],
                options=poll_data["options"],
                is_anonymous=True,
            )
            storage.record_promo_post("poll", poll_data["question"], msg.message_id)
            logger.info(f"Published engagement poll: msg_id={msg.message_id}")
        except Exception as e:
            logger.error(f"Failed to post poll: {e}")


promo = PromoEngine()
