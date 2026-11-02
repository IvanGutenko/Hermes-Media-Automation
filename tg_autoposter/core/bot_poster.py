import asyncio
import time
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, List, Tuple, Any
import httpx
from telegram import Bot
from telegram.request import HTTPXRequest
from telegram.error import TelegramError, RetryAfter, Forbidden

from .config import config
from .editorial_policy import allowed

logger = logging.getLogger(__name__)


class BotPoster:
    """Публикация в канал @WrongDevp через официальный Telegram Bot API с поддержкой видео, фото и прокси."""

    def __init__(self):
        self.bot: Optional[Bot] = None
        self._last_post_time = 0
        self._min_interval = config.processing.min_post_interval_sec

    def set_bot(self, bot_instance: Bot):
        self.bot = bot_instance

    async def init(self):
        if not config.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not configured")

        request = HTTPXRequest(
            proxy=config.proxy,
            connect_timeout=15.0,
            read_timeout=60.0,
            write_timeout=60.0
        )
        self.bot = Bot(token=config.bot_token, request=request)
        me = await self.bot.get_me()
        logger.info(f"Bot poster initialized: @{me.username} targeting {config.target_channel}")
        await self._check_admin_rights()

    async def _check_admin_rights(self):
        try:
            chat = await self.bot.get_chat(config.target_channel)
            bot_me = await self.bot.get_me()
            member = await self.bot.get_chat_member(chat.id, bot_me.id)
            if member.status not in ("administrator", "creator"):
                logger.warning(f"Bot is not admin in {config.target_channel}: status={member.status}")
            else:
                logger.info(f"Verified admin rights in channel: {chat.title} ({chat.id})")
        except Exception as e:
            logger.error(f"Channel access check error: {e}")

    def is_within_active_hours(self) -> bool:
        start_hour = config.processing.active_hours.get("start", 8)
        end_hour = config.processing.active_hours.get("end", 23)
        try:
            tz_name = getattr(config.processing, "timezone", "Europe/Moscow")
            tz = ZoneInfo(tz_name)
            current_hour = datetime.now(tz).hour
        except Exception:
            current_hour = datetime.now().hour

        if start_hour <= end_hour:
            return start_hour <= current_hour < end_hour
        else:
            return current_hour >= start_hour or current_hour < end_hour

    def _parse_media_item(self, item: Any) -> Tuple[str, str, Optional[str]]:
        """Определяет тип медиа (video/photo), URL контента и превью (thumb_url)."""
        if isinstance(item, dict):
            m_type = item.get("type", "photo")
            url = item.get("url", "")
            thumb = item.get("thumb_url")
            return m_type, url, thumb
        elif isinstance(item, str):
            url = item
            if ".mp4" in url.lower():
                return "video", url, None
            return "photo", url, None
        return "photo", "", None

    async def _download_media(self, url: str) -> Tuple[Optional[bytes], Optional[str]]:
        """Загружает файл через прокси с заголовками браузера и возвращает (байты, content_type)."""
        if not url:
            return None, None

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Referer": "https://t.me/",
            "Accept": "*/*",
        }
        try:
            async with httpx.AsyncClient(proxy=config.proxy, timeout=60.0, follow_redirects=True, headers=headers) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "")
                    return resp.content, content_type
                else:
                    logger.warning(f"Media download failed {url}: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Failed to download media from {url}: {e}")
        return None, None

    async def post_article(
        self,
        html_text: str,
        title: str = "",
        media_urls: Optional[List[Any]] = None,
        source_id: Optional[str] = None
    ) -> Optional[int]:
        """Публикует пост в целевой канал с учетом видео/фото и лимитов длины."""
        if not allowed(title + " " + re.sub(r"<[^>]+>", " ", html_text)):
            raise ValueError("Post excluded by owner topic policy")
        if not self.bot:
            await self.init()

        # Проверка временного окна активности
        if not self.is_within_active_hours():
            logger.info("Outside of active hours, delaying post")
            return None

        # Rate limiting: пауза между постами в канале
        now = time.time()
        time_since_last = now - self._last_post_time
        if time_since_last < self._min_interval:
            wait_time = self._min_interval - time_since_last
            logger.info(f"Rate limiting: waiting {wait_time:.1f}s before next post")
            await asyncio.sleep(wait_time)

        media_bytes = None
        media_type = "photo"
        thumb_bytes = None

        if media_urls and len(media_urls) > 0:
            media_item = media_urls[0]
            m_type, m_url, thumb_url = self._parse_media_item(media_item)

            raw_bytes, content_type = await self._download_media(m_url)
            if raw_bytes:
                media_bytes = raw_bytes
                if m_type == "video" or (content_type and "video" in content_type.lower()) or ".mp4" in m_url.lower():
                    media_type = "video"
                else:
                    media_type = "photo"

            # Для видео также скачиваем обложку (thumbnail), если она есть
            if media_type == "video" and thumb_url:
                t_bytes, _ = await self._download_media(thumb_url)
                thumb_bytes = t_bytes

        if not media_bytes:
            # The stable DB source id shares the same generated asset with VK.
            # Generation failures keep the post queued instead of sending bare text.
            import sys
            from pathlib import Path
            sys.path.insert(0, '/home/gut/hermes-workspace/video_factory/scripts')
            from post_images import ensure_image
            if not source_id:
                import hashlib
                source_id = 'text-' + hashlib.sha256(html_text.encode()).hexdigest()
            image_path = await asyncio.to_thread(ensure_image, 'wrongdev', source_id, title, html_text)
            media_bytes = Path(image_path).read_bytes()
            media_type = 'photo'

        try:
            # 1. Публикация ВИДЕО
            if media_bytes and media_type == "video":
                # Лимит Telegram Bot API: 50MB
                if len(media_bytes) > 50 * 1024 * 1024:
                    logger.warning(f"Video size ({len(media_bytes)} bytes) exceeds Telegram 50MB limit. Falling back to thumbnail.")
                    if thumb_bytes:
                        media_bytes = thumb_bytes
                        media_type = "photo"
                    else:
                        return await self._send_text_message(html_text)

            if media_bytes and media_type == "video":
                logger.info(f"Sending post as VIDEO ({len(media_bytes)} bytes) to {config.target_channel}")
                # Лимит подписи в Telegram: 1024 символа
                if len(html_text) <= 1024:
                    msg = await self.bot.send_video(
                        chat_id=config.target_channel,
                        video=media_bytes,
                        caption=html_text,
                        parse_mode="HTML",
                        supports_streaming=True,
                        thumbnail=thumb_bytes,
                        write_timeout=60.0,
                        read_timeout=60.0
                    )
                    self._last_post_time = time.time()
                    return msg.message_id

                compact_html = re.sub(r"\n{3,}", "\n\n", html_text).strip()
                if len(compact_html) <= 1024:
                    msg = await self.bot.send_video(
                        chat_id=config.target_channel,
                        video=media_bytes,
                        caption=compact_html,
                        parse_mode="HTML",
                        supports_streaming=True,
                        thumbnail=thumb_bytes,
                        write_timeout=60.0,
                        read_timeout=60.0
                    )
                    self._last_post_time = time.time()
                    return msg.message_id

                # Если текст длиннее 1024: видео с заголовком + полный текст отдельным сообщением
                short_caption = f"<b>{title}</b>" if title else ""
                await self.bot.send_video(
                    chat_id=config.target_channel,
                    video=media_bytes,
                    caption=short_caption,
                    parse_mode="HTML",
                    supports_streaming=True,
                    thumbnail=thumb_bytes,
                    write_timeout=60.0,
                    read_timeout=60.0
                )
                await asyncio.sleep(1.0)
                msg = await self.bot.send_message(
                    chat_id=config.target_channel,
                    text=html_text,
                    parse_mode="HTML",
                    disable_web_page_preview=False
                )
                self._last_post_time = time.time()
                return msg.message_id

            # 2. Публикация ФОТО
            elif media_bytes and media_type == "photo":
                logger.info(f"Sending post as PHOTO ({len(media_bytes)} bytes) to {config.target_channel}")
                if len(html_text) <= 1024:
                    msg = await self.bot.send_photo(
                        chat_id=config.target_channel,
                        photo=media_bytes,
                        caption=html_text,
                        parse_mode="HTML",
                        write_timeout=30.0,
                        read_timeout=30.0
                    )
                    self._last_post_time = time.time()
                    return msg.message_id

                compact_html = re.sub(r"\n{3,}", "\n\n", html_text).strip()
                if len(compact_html) <= 1024:
                    msg = await self.bot.send_photo(
                        chat_id=config.target_channel,
                        photo=media_bytes,
                        caption=compact_html,
                        parse_mode="HTML",
                        write_timeout=30.0,
                        read_timeout=30.0
                    )
                    self._last_post_time = time.time()
                    return msg.message_id

                short_caption = f"<b>{title}</b>" if title else ""
                await self.bot.send_photo(
                    chat_id=config.target_channel,
                    photo=media_bytes,
                    caption=short_caption,
                    parse_mode="HTML",
                    write_timeout=30.0,
                    read_timeout=30.0
                )
                await asyncio.sleep(1.0)
                msg = await self.bot.send_message(
                    chat_id=config.target_channel,
                    text=html_text,
                    parse_mode="HTML",
                    disable_web_page_preview=False
                )
                self._last_post_time = time.time()
                return msg.message_id

            # 3. Публикация ТЕКСТА
            else:
                logger.info(f"Sending post as TEXT to {config.target_channel}")
                return await self._send_text_message(html_text)

        except RetryAfter as e:
            logger.warning(f"Telegram flood control: waiting {e.retry_after}s")
            await asyncio.sleep(e.retry_after + 1)
            return await self.post_article(html_text, title, media_urls)

        except Forbidden:
            logger.error(f"Bot forbidden in {config.target_channel}. Check admin permissions.")
            return None

        except TelegramError as e:
            logger.error(f"Telegram API posting error: {e}")
            if "can't parse entities" in str(e).lower():
                logger.info("Attempting fallback text post without HTML tags...")
                plain_text = re.sub(r"<[^>]+>", "", html_text)
                try:
                    msg = await self.bot.send_message(
                        chat_id=config.target_channel,
                        text=plain_text
                    )
                    self._last_post_time = time.time()
                    return msg.message_id
                except Exception as ex2:
                    logger.error(f"Fallback plain text post also failed: {ex2}")
            return None

    async def _send_text_message(self, html_text: str) -> Optional[int]:
        msg = await self.bot.send_message(
            chat_id=config.target_channel,
            text=html_text,
            parse_mode="HTML",
            disable_web_page_preview=False
        )
        self._last_post_time = time.time()
        return msg.message_id


bot_poster = BotPoster()
