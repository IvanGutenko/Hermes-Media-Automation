import asyncio
import logging
from datetime import datetime
from typing import Optional, List
from telethon import TelegramClient, events
from telethon.tl.types import Channel, Message
from telethon.errors import FloodWaitError, ChannelPrivateError

from .config import config
from .storage import storage
from .processor import processor
from .bot_poster import bot_poster

logger = logging.getLogger(__name__)


class UserbotListener:
    """Telethon userbot: слушает новые сообщения в исходных каналах"""

    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.resolved_channels: dict = {}  # username -> channel_id
        self._running = False

    async def start(self):
        """Инициализация и запуск клиента"""
        self.client = TelegramClient(
            config.session_name,
            config.telegram_api_id,
            config.telegram_api_hash,
            system_version="4.16.30-vxCUSTOM",
        )

        await self.client.start()
        me = await self.client.get_me()
        logger.info(f"Userbot started as {me.first_name} (@{me.username})")

        # Резолвим username -> channel_id для всех источников
        await self._resolve_channels()

        # Регистрируем хендлер
        @self.client.on(events.NewMessage(chats=list(self.resolved_channels.values())))
        async def handler(event: events.NewMessage.Event):
            await self._handle_message(event)

        self._running = True
        logger.info(f"Listening to {len(self.resolved_channels)} channels")

    async def _resolve_channels(self):
        """Преобразует usernames в numeric IDs"""
        for src in config.source_channels:
            username = src.username.lstrip("@")
            # Сначала пробуем из кэша
            cached_id = storage.get_channel_id(username)
            if cached_id:
                self.resolved_channels[username] = cached_id
                src.channel_id = cached_id
                continue

            try:
                entity = await self.client.get_entity(username)
                if isinstance(entity, Channel):
                    channel_id = entity.id
                    self.resolved_channels[username] = channel_id
                    src.channel_id = channel_id
                    storage.save_channel_id(username, channel_id)
                    logger.info(f"Resolved @{username} -> {channel_id}")
                else:
                    logger.warning(f"@{username} is not a channel")
            except ChannelPrivateError:
                logger.warning(f"@{username} is private or no access")
            except Exception as e:
                logger.error(f"Failed to resolve @{username}: {e}")

    async def _handle_message(self, event: events.NewMessage.Event):
        """Обработка нового сообщения"""
        msg: Message = event.message
        if not msg or not msg.text:
            return

        # Определяем источник
        chat = await event.get_chat()
        source_username = getattr(chat, "username", None) or str(chat.id)
        source_msg_id = msg.id
        source_date = msg.date

        logger.info(f"New message from @{source_username} (id={source_msg_id})")

        # Проверяем, не обрабатывали ли уже
        existing = storage.get_post_by_source(source_username, source_msg_id)
        if existing:
            logger.debug("Already processed, skipping")
            return

        # Обрабатываем через пайплайн
        result = processor.process(msg.text, source_username, source_msg_id, source_date)

        if result["status"] == "duplicate":
            storage.increment_metric(datetime.now().strftime("%Y-%m-%d"), "posts_skipped_dup")
            logger.info(f"Duplicate skipped: {result['reason']}")
            return

        if result["status"] == "skipped":
            storage.increment_metric(datetime.now().strftime("%Y-%m-%d"), "posts_skipped_ads")
            logger.info(f"Skipped: {result['reason']}")
            return

        # Сохраняем в БД
        post_id = storage.save_post(
            source_channel=source_username,
            source_msg_id=source_msg_id,
            source_date=source_date,
            original_text=result["original"],
            processed_text=result["processed"],
            metadata={"cleaned": result["cleaned"]},
        )

        # Публикуем в целевой канал через бота
        try:
            target_msg_id = await bot_poster.post(result["processed"])
            if target_msg_id:
                storage.mark_posted(post_id, target_msg_id)
                storage.increment_metric(datetime.now().strftime("%Y-%m-%d"), "posts_published")
                logger.info(f"Posted to target: msg_id={target_msg_id}")
            else:
                logger.error("Failed to post to target channel")
        except Exception as e:
            logger.error(f"Post failed: {e}")

    async def run_forever(self):
        """Запускает клиент до отмены"""
        if not self.client:
            await self.start()
        try:
            await self.client.run_until_disconnected()
        finally:
            self._running = False

    async def stop(self):
        if self.client:
            await self.client.disconnect()
        self._running = False


# Singleton
userbot = UserbotListener()