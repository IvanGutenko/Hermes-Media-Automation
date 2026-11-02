import asyncio
import logging
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
import httpx
from bs4 import BeautifulSoup

from .config import config
from .storage import storage

logger = logging.getLogger(__name__)


class WebChannelCollector:
    """Сбор постов из открытых каналов Telegram через веб-интерфейс t.me/s/...
    Работает надежно, без бана аккаунтов, без необходимости вводить телефон или 2FA.
    """

    def __init__(self):
        self.proxy = config.proxy
        self._running = False

    async def fetch_channel_posts(self, channel_username: str) -> List[Dict[str, Any]]:
        """Загружает страницу канала и возвращает список разобранных сообщений."""
        clean_username = channel_username.lstrip("@")
        url = f"https://t.me/s/{clean_username}"
        
        try:
            async with httpx.AsyncClient(proxy=self.proxy, timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                })
                
            if resp.status_code != 200:
                logger.warning(f"Failed to fetch {url}: HTTP {resp.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"Network error fetching {url}: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        message_divs = soup.find_all("div", class_="tgme_widget_message")
        
        parsed_posts = []
        for m in message_divs:
            data_post = m.get("data-post")
            if not data_post or "/" not in data_post:
                continue
                
            try:
                ch, raw_id = data_post.split("/", 1)
                msg_id = int(raw_id)
            except ValueError:
                continue

            # Извлекаем дату публикации
            time_elem = m.find("time")
            msg_date = time_elem.get("datetime") if time_elem else datetime.now().isoformat()

            # Извлекаем текст
            text_div = m.find("div", class_="tgme_widget_message_text")
            raw_text = text_div.get_text(separator="\n").strip() if text_div else ""

            # Извлекаем медиа (видео, анимации, фотографии)
            media_urls = []

            # 1. Проверяем видео (<video src="..."> или <source src="...">)
            video_tags = m.find_all("video")
            for v in video_tags:
                src = v.get("src")
                if not src:
                    source_elem = v.find("source")
                    if source_elem:
                        src = source_elem.get("src")
                if src:
                    thumb_url = None
                    parent_player = v.find_parent("a", class_="tgme_widget_message_video_player")
                    if parent_player:
                        thumb_elem = parent_player.find("i", class_="tgme_widget_message_video_thumb")
                        if thumb_elem:
                            style = thumb_elem.get("style", "")
                            match = re.search(r"background-image:url\('([^']+)'\)", style)
                            if match:
                                thumb_url = match.group(1)

                    media_urls.append({
                        "type": "video",
                        "url": src,
                        "thumb_url": thumb_url
                    })

            # 2. Проверяем фотографии (photo_wraps)
            photo_wraps = m.find_all("a", class_="tgme_widget_message_photo_wrap")
            for pw in photo_wraps:
                style = pw.get("style", "")
                match = re.search(r"background-image:url\('([^']+)'\)", style)
                if match:
                    p_url = match.group(1)
                    if not any(isinstance(x, dict) and (x.get("thumb_url") == p_url or x.get("url") == p_url) for x in media_urls):
                        media_urls.append({
                            "type": "photo",
                            "url": p_url
                        })

            # 3. Fallback: если видеотег не найден, но есть превью
            if not media_urls:
                video_thumbs = m.find_all("i", class_="tgme_widget_message_video_thumb")
                for vt in video_thumbs:
                    style = vt.get("style", "")
                    match = re.search(r"background-image:url\('([^']+)'\)", style)
                    if match:
                        media_urls.append({
                            "type": "photo",
                            "url": match.group(1)
                        })

            parsed_posts.append({
                "channel": clean_username,
                "msg_id": msg_id,
                "date": msg_date,
                "raw_text": raw_text,
                "media_urls": media_urls,
            })

        return parsed_posts

    async def collect_new_posts(self) -> List[Dict[str, Any]]:
        """Опрашивает все сконфигурированные каналы и возвращает только НОВЫЕ посты."""
        new_posts = []
        
        for src in config.source_channels:
            username = src.username.lstrip("@")
            logger.debug(f"Checking channel: @{username}")
            
            posts = await self.fetch_channel_posts(username)
            if not posts:
                await asyncio.sleep(config.collector.channel_delay_sec)
                continue

            # Сортируем по ID от старых к новым
            posts.sort(key=lambda p: p["msg_id"])
            latest_remote_id = posts[-1]["msg_id"]
            last_seen_id = storage.get_channel_last_seen_id(username)

            if last_seen_id == 0:
                # Первый запуск для этого канала: берем максимум initial_backfill_limit постов
                backfill_limit = max(0, config.collector.initial_backfill_limit)
                start_index = max(0, len(posts) - backfill_limit)
                eligible = posts[start_index:]
                logger.info(f"First run for @{username}: baseline set to {latest_remote_id}, queuing {len(eligible)} post(s)")
            else:
                eligible = [p for p in posts if p["msg_id"] > last_seen_id]

            for p in eligible:
                # Фильтруем пустые посты без текста
                if not p["raw_text"] or len(p["raw_text"]) < 40:
                    continue
                    
                # Проверяем, нет ли уже в БД
                existing = storage.get_post_by_source(p["channel"], p["msg_id"])
                if not existing:
                    post_id = storage.record_collected_post(
                        source_channel=p["channel"],
                        source_msg_id=p["msg_id"],
                        source_date=p["date"],
                        original_text=p["raw_text"],
                        media_urls=p["media_urls"],
                    )
                    p["db_post_id"] = post_id
                    new_posts.append(p)

            # Обновляем максимальный увиденный ID
            storage.set_channel_last_seen_id(username, latest_remote_id)
            await asyncio.sleep(config.collector.channel_delay_sec)

        return new_posts


collector = WebChannelCollector()
