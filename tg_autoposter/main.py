import asyncio
import logging
import signal
import sys
from pathlib import Path
from telegram.request import HTTPXRequest
from telegram.ext import ApplicationBuilder

# Логирование
log_dir = Path(__file__).parent / "logs"
log_dir.mkdir(exist_ok=True)
log_file = log_dir / "tg_autoposter.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

# Глушим избыточный шум библиотек
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("autoposter")

from core.config import config
from core.collector import collector
from core.processor import processor, strip_source_footer
from core.bot_poster import bot_poster
from core.promo import promo
from core.analytics import analytics
from core.storage import storage
from core.dedup import dedup
from core.editorial_policy import allowed
from core.admin import AdminInterface


class AutoposterApp:
    def __init__(self):
        self._shutdown = False
        self.is_paused = False
        self.tg_app = None
        self.admin = AdminInterface(self)
        self._check_lock = asyncio.Lock()
        self._wake_event = asyncio.Event()

    async def run(self):
        logger.info("=" * 60)
        logger.info("Starting Telegram AI Autoposter & Admin Bot for @WrongDevp")
        logger.info(f"Target Channel: {config.target_channel}")
        logger.info(f"Admin IDs: {config.admin_ids}")
        logger.info(f"Sources: {[s.username for s in config.source_channels]}")
        logger.info(f"Proxy: {config.proxy}")
        logger.info(f"LLM Provider: {config.llm.provider} (model: {config.llm.model})")
        logger.info("=" * 60)

        # 1. Инициализация Application с поддержкой прокси для команд и сообщений
        # Long polling must not occupy the pool used by channel publication,
        # analytics, and admin responses. Each client owns its own lifecycle.
        request = HTTPXRequest(proxy=config.proxy, connect_timeout=15.0,
            read_timeout=30.0, connection_pool_size=8, pool_timeout=15.0)
        polling_request = HTTPXRequest(proxy=config.proxy, connect_timeout=15.0,
            read_timeout=40.0, connection_pool_size=1, pool_timeout=15.0)
        self.tg_app = (
            ApplicationBuilder()
            .token(config.bot_token)
            .request(request)
            .get_updates_request(polling_request)
            .build()
        )

        # Связываем bot_poster с ботом
        bot_poster.set_bot(self.tg_app.bot)
        await bot_poster._check_admin_rights()

        # 2. Регистрируем админ-панель (команды /start, /status, /check, кнопки меню)
        self.admin.register_handlers(self.tg_app)

        # Запускаем polling входящих команд от администратора
        await self.tg_app.initialize()
        await self.tg_app.start()
        await self.tg_app.updater.start_polling(poll_interval=1.5, drop_pending_updates=True)
        logger.info("Admin bot polling started. You can control the bot in Telegram PM!")

        # 3. Запуск фонового промо-движка и аналитики
        promo_task = asyncio.create_task(promo.run_periodic())
        analytics_task = asyncio.create_task(analytics.run_periodic())

        # 4. Основной цикл сбора и публикации
        try:
            while not self._shutdown:
                if not self.is_paused:
                    try:
                        await self.run_check_cycle()
                    except Exception as e:
                        logger.error(f"Error during collection cycle: {e}", exc_info=True)
                else:
                    logger.debug("Autoposter is currently paused by admin.")

                # Пауза между проверками каналов
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=config.collector.poll_interval_sec)
                    self._wake_event.clear()
                except asyncio.TimeoutError:
                    pass

        finally:
            promo_task.cancel()
            analytics_task.cancel()
            logger.info("Shutting down admin bot and services...")
            if self.tg_app and self.tg_app.updater:
                await self.tg_app.updater.stop()
            if self.tg_app:
                await self.tg_app.stop()
                await self.tg_app.shutdown()
            logger.info("Autoposter terminated cleanly.")

    async def run_manual_check(self):
        """Ручной запуск цикла проверки из админ-меню Telegram."""
        return await self.run_check_cycle()

    async def run_check_cycle(self):
        """Один полный цикл: сбор новых постов, LLM-обработка и публикация очереди."""
        async with self._check_lock:
            # 1. Сбор новых постов с каналов-доноров
            logger.info("Checking source channels for new posts...")
            new_posts = await collector.collect_new_posts()
            q = storage.get_queue_count()
            if new_posts:
                logger.info(f"Collected {len(new_posts)} new raw post(s). Queue: {q['pending']} pending.")
            else:
                logger.info(f"No new posts scraped. Queue: {q['pending']} pending.")

            # 2. Обработка всех необработанных постов со статусом 'collected'
            unprocessed = storage.get_unprocessed_posts(limit=10)
            if unprocessed:
                logger.info(f"Processing {len(unprocessed)} unhandled collected post(s)...")

            for post in unprocessed:
                if self._shutdown or self.is_paused:
                    break

                db_id = post["id"]
                ch = post["source_channel"]
                msg_id = post["source_msg_id"]
                raw_text = post.get("original_text", "")
                if not allowed(raw_text):
                    storage.mark_policy_skipped(db_id)
                    logger.info("Post %s excluded by owner topic policy", db_id)
                    continue
                media_urls = post.get("media_urls", [])

                logger.info(f"--- Processing post from @{ch} (id={msg_id}, db_id={db_id}) ---")
                storage.increment_metric("posts_collected")

                # Обрабатываем через пайплайн
                result = processor.process(raw_text, ch, msg_id)
                status = result.get("status")

                if status == "skip_ad":
                    reason = result.get("reason", "ad")
                    logger.info(f"SKIPPED (Ad detected: {reason})")
                    storage.mark_skipped(db_id, reason=reason, is_ad=True)
                    storage.increment_metric("posts_skipped_ads")
                    continue

                elif status == "skip_dup":
                    reason = result.get("reason", "dup")
                    logger.info(f"SKIPPED (Duplicate detected: {reason})")
                    storage.mark_skipped(db_id, reason=reason, is_ad=False)
                    storage.increment_metric("posts_skipped_dup")
                    continue

                elif status != "ok":
                    reason = result.get("reason", "unknown")
                    logger.warning(f"SKIPPED (Status {status}: {reason})")
                    storage.mark_skipped(db_id, reason=reason, is_ad=False)
                    continue

                # Пост одобрен — сохраняем в очередь со статусом 'pending'
                title = result.get("title", "")
                key_topic = result.get("key_topic", "")
                html_content = result.get("html", "")

                logger.info(f"Post approved! Topic='{key_topic}', Title='{title}' -> Queued as pending")
                storage.mark_pending(
                    post_id=db_id,
                    processed_text=html_content,
                    title=title,
                    key_topic=key_topic,
                    metadata={"source": ch, "source_id": msg_id, "topic": key_topic, "media_urls": media_urls}
                )

                # growth_post_draft: only a newly written, persisted draft is
                # attributed; historical queued posts are never retro-tagged.
                try:
                    from growth_actions import record
                    record('wrongdev', 'post_draft', result.get('growth_actions', {}), source_id='post-' + str(db_id))
                except Exception as exc:
                    logger.warning('Editorial assignment record unavailable: %s', type(exc).__name__)

            # 3. Публикация очереди pending
            posted_count = 0
            if not bot_poster.is_within_active_hours():
                q = storage.get_queue_count()
                logger.info(
                    f"Outside of active hours ({config.processing.active_hours.get('start', 8)}-{config.processing.active_hours.get('end', 23)} {getattr(config.processing, 'timezone', 'Europe/Moscow')}). "
                    f"Queue: {q['pending']} pending post(s) waiting."
                )
                return len(new_posts), 0

            # Если мы внутри активных часов — публикуем готовые посты
            pending_posts = storage.get_pending_posts(limit=1)
            for post in pending_posts:
                if self._shutdown or self.is_paused:
                    break

                db_id = post["id"]
                title = post.get("title", "")
                key_topic = post.get("key_topic", "")
                html_content = strip_source_footer(post.get("processed_text", ""))
                media_urls = post.get("media_urls", [])
                text_hash = post.get("text_hash", "")
                ch = post.get("source_channel", "")
                msg_id = post.get("source_msg_id", 0)

                if not allowed(title + " " + html_content + " " + post.get("original_text", "")):
                    storage.mark_policy_skipped(db_id)
                    logger.info("Queued post %s excluded by owner topic policy", db_id)
                    continue

                # Проверяем дедупликацию перед самой публикацией
                is_dup, dup_reason = dedup.is_duplicate(
                    raw_text=html_content or post.get("original_text", ""),
                    key_topic=key_topic,
                    title=title
                )
                if is_dup:
                    logger.info(f"Pending post {db_id} skipped: duplicate ({dup_reason}) while waiting in queue")
                    storage.mark_skipped(db_id, reason=f"Duplicate while queued: {dup_reason}", is_ad=False)
                    storage.increment_metric("posts_skipped_dup")
                    continue

                logger.info(f"Publishing pending post {db_id} from @{ch} ({title})...")
                target_msg_id = await bot_poster.post_article(
                    html_text=html_content,
                    title=title,
                    media_urls=media_urls,
                    source_id="post-" + str(db_id)
                )

                if target_msg_id:
                    storage.mark_posted(
                        post_id=db_id,
                        target_msg_id=target_msg_id,
                        processed_text=html_content,
                        title=title,
                        key_topic=key_topic,
                        metadata={"source": ch, "source_id": msg_id, "topic": key_topic}
                    )
                    storage.increment_metric("posts_published")
                    dedup.record_published(
                        post_id=db_id,
                        text_hash=text_hash,
                        key_topic=key_topic,
                        title=title
                    )
                    posted_count += 1
                    logger.info(f"SUCCESS: Published to {config.target_channel} (msg_id={target_msg_id})")
                else:
                    logger.warning(f"Post {db_id} could not be sent right now, will retry in next cycle.")

            return len(new_posts), posted_count

    def stop(self):
        self._shutdown = True
        self._wake_event.set()


async def main():
    app = AutoposterApp()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: app.stop())
        except NotImplementedError:
            pass

    await app.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Exiting...")
