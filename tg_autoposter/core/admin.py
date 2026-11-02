import asyncio
import logging
from datetime import datetime
from typing import Optional, List
import yaml
from pathlib import Path

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from .config import config
from .storage import storage
from .promo import promo
from .analytics import analytics

logger = logging.getLogger(__name__)

KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📊 Статус"), KeyboardButton("🔄 Проверить сейчас")],
        [KeyboardButton("🧠 Анализ постов"), KeyboardButton("📈 Статистика")],
        [KeyboardButton("📋 Список каналов"), KeyboardButton("📰 Выпустить дайджест")],
        [KeyboardButton("⏸ Пауза / ▶️ Пуск")],
    ],
    resize_keyboard=True,
)


class AdminInterface:
    def __init__(self, autoposter_app):
        self.app = autoposter_app
        self.admin_ids = config.admin_ids

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    def register_handlers(self, tg_app: Application):
        tg_app.add_handler(CommandHandler("start", self.cmd_start))
        tg_app.add_handler(CommandHandler("help", self.cmd_help))
        tg_app.add_handler(CommandHandler("status", self.cmd_status))
        tg_app.add_handler(CommandHandler("check", self.cmd_check))
        tg_app.add_handler(CommandHandler("pause", self.cmd_pause))
        tg_app.add_handler(CommandHandler("resume", self.cmd_resume))
        tg_app.add_handler(CommandHandler("stats", self.cmd_stats))
        tg_app.add_handler(CommandHandler("channels", self.cmd_channels))
        tg_app.add_handler(CommandHandler("add", self.cmd_add_channel))
        tg_app.add_handler(CommandHandler("del", self.cmd_del_channel))
        tg_app.add_handler(CommandHandler("digest", self.cmd_digest))
        tg_app.add_handler(CommandHandler("analytics", self.cmd_analytics))
        tg_app.add_handler(CommandHandler("analyze", self.cmd_analytics))

        # Текстовые кнопки меню
        tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_menu_button))

    async def check_auth(self, update: Update) -> bool:
        user_id = update.effective_user.id if update.effective_user else 0
        if not self.is_admin(user_id):
            if update.message:
                await update.message.reply_text("⛔️ Доступ запрещен. Бот управляется только владельцем.")
            return False
        return True

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update):
            return
        text = (
            "👋 <b>Панель управления WrongDev Autoposter</b>\n\n"
            "Вы можете управлять автопостингом, проверять источники, смотреть статистику и добавлять каналы прямо из этого чата.\n\n"
            "Выберите действие в меню ниже или отправьте команду."
        )
        await update.message.reply_html(text, reply_markup=KEYBOARD)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update):
            return
        text = (
            "🛠 <b>Доступные команды:</b>\n\n"
            "• /status — текущий статус службы и фильтрации\n"
            "• /check — немедленно проверить каналы и опубликовать свежее\n"
            "• /pause — приостановить автопостинг\n"
            "• /resume — возобновить автопостинг\n"
            "• /analytics — анализ популярности постов и стратегии тем\n"
            "• /stats — статистика за последние 7 дней\n"
            "• /channels — список источников\n"
            "• /add &lt;username&gt; — добавить новый канал-донор\n"
            "• /del &lt;username&gt; — удалить канал-донор\n"
            "• /digest — принудительно выпустить дайджест в канал"
        )
        await update.message.reply_html(text, reply_markup=KEYBOARD)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update):
            return

        state_emoji = "⏸ На паузе" if self.app.is_paused else "🟢 Активен (мониторинг)"
        now_date = datetime.now().strftime("%Y-%m-%d")
        
        # Загрузим метрики за сегодня
        today_metrics = {"posts_collected": 0, "posts_published": 0, "posts_skipped_ads": 0, "posts_skipped_dup": 0}
        with storage.transaction() as conn:
            row = conn.execute("SELECT * FROM metrics WHERE date=?", (now_date,)).fetchone()
            if row:
                today_metrics = dict(row)

            # Последний опубликованный пост
            last_post = conn.execute(
                "SELECT title, posted_date, source_channel FROM posts WHERE posted_to_target=TRUE ORDER BY posted_date DESC LIMIT 1"
            ).fetchone()

        last_post_text = "Нет публикаций"
        if last_post:
            last_title = last_post["title"] or "Без заголовка"
            p_time = last_post["posted_date"][:16].replace("T", " ")
            last_post_text = f"«{last_title}» (@{last_post['source_channel']}, {p_time})"

        q = storage.get_queue_count()

        text = (
            f"📊 <b>Статус WrongDev Autoposter:</b>\n\n"
            f"• <b>Режим:</b> {state_emoji}\n"
            f"• <b>Целевой канал:</b> {config.target_channel}\n"
            f"• <b>Источников:</b> {len(config.source_channels)} каналов\n"
            f"• <b>Интервал опроса:</b> {config.collector.poll_interval_sec} сек\n"
            f"• <b>Мин. пауза между постами:</b> {config.processing.min_post_interval_sec // 60} мин\n"
            f"• <b>Очередь постов:</b> <b>{q['pending']}</b> готово к выходу ({q['collected']} необработано)\n\n"
            f"📈 <b>За сегодня ({now_date}):</b>\n"
            f"• Собрано постов: <b>{today_metrics.get('posts_collected', 0)}</b>\n"
            f"• Опубликовано: <b>{today_metrics.get('posts_published', 0)}</b>\n"
            f"• Отсеяно рекламы: <b>{today_metrics.get('posts_skipped_ads', 0)}</b>\n"
            f"• Отсеяно дубликатов: <b>{today_metrics.get('posts_skipped_dup', 0)}</b>\n\n"
            f"📌 <b>Последний пост в канале:</b>\n{last_post_text}\n\n"
            f"🧠 <b>LLM:</b> {config.llm.provider} ({config.llm.model})\n"
            f"🛡 <b>Прокси:</b> {config.proxy} (OK)"
        )
        await update.message.reply_html(text, reply_markup=KEYBOARD)

    async def cmd_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update):
            return

        msg = await update.message.reply_text("🔄 Опрашиваю каналы-источники и запускаю пайплайн...")
        try:
            collected_count, posted_count = await self.app.run_manual_check()
            q = storage.get_queue_count()
            await msg.edit_text(
                f"✅ <b>Проверка завершена!</b>\n\n"
                f"• Найдено новых постов: <b>{collected_count}</b>\n"
                f"• Опубликовано в @WrongDevp: <b>{posted_count}</b>\n"
                f"• В очереди публикации: <b>{q['pending']}</b> готово ({q['collected']} необработано)\n\n"
                f"(Посты публикуются автоматически с паузой {config.processing.min_post_interval_sec // 60} мин)",
                parse_mode="HTML"
            )
        except Exception as e:
            await msg.edit_text(f"❌ Ошибка при проверке: {e}")

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update):
            return
        self.app.is_paused = True
        await update.message.reply_text("⏸ <b>Автопостинг приостановлен.</b> Новые посты в канал публиковаться не будут.", parse_mode="HTML", reply_markup=KEYBOARD)

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update):
            return
        self.app.is_paused = False
        await update.message.reply_text("▶️ <b>Автопостинг возобновлен.</b> Мониторинг каналов активен.", parse_mode="HTML", reply_markup=KEYBOARD)

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update):
            return

        with storage.transaction() as conn:
            rows = conn.execute("SELECT * FROM metrics ORDER BY date DESC LIMIT 7").fetchall()

        if not rows:
            await update.message.reply_text("Статистика пока пуста. Данные появятся после первых циклов работы.")
            return

        lines = ["📈 <b>Статистика работы за последние дни:</b>\n"]
        for r in rows:
            d = dict(r)
            lines.append(
                f"📅 <b>{d['date']}</b>\n"
                f"  • Собрано: {d.get('posts_collected', 0)} | Опубликовано: {d.get('posts_published', 0)}\n"
                f"  • Спам отсеян: {d.get('posts_skipped_ads', 0)} | Дубликаты: {d.get('posts_skipped_dup', 0)}"
            )

        await update.message.reply_html("\n\n".join(lines), reply_markup=KEYBOARD)

    async def cmd_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update):
            return

        lines = ["📋 <b>Каналы-источники (AI / Dev):</b>\n"]
        for i, ch in enumerate(config.source_channels, 1):
            name = ch.title or ch.username
            url = ch.original_url or f"t.me/{ch.username}"
            lines.append(f"{i}. <b>{name}</b> — <a href=\"https://{url}\">@{ch.username}</a>")

        lines.append("\n<i>Добавить канал:</i> <code>/add username</code>")
        lines.append("<i>Удалить канал:</i> <code>/del username</code>")

        await update.message.reply_html("\n".join(lines), disable_web_page_preview=True, reply_markup=KEYBOARD)

    async def cmd_add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update):
            return

        if not context.args:
            await update.message.reply_text("Использование: /add <username>\nПример: /add ai_news_channel")
            return

        new_ch = context.args[0].lstrip("@").strip()
        existing = [c.username for c in config.source_channels]
        if new_ch in existing:
            await update.message.reply_text(f"Канал @{new_ch} уже есть в списке источников.")
            return

        from .config import SourceChannel
        config.source_channels.append(SourceChannel(username=new_ch, title=new_ch))
        self._save_channels_to_yaml()
        await update.message.reply_text(f"✅ Канал @{new_ch} успешно добавлен в мониторинг!")

    async def cmd_del_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update):
            return

        if not context.args:
            await update.message.reply_text("Использование: /del <username>\nПример: /del codexsale")
            return

        target_ch = context.args[0].lstrip("@").strip()
        config.source_channels = [c for c in config.source_channels if c.username != target_ch]
        self._save_channels_to_yaml()
        await update.message.reply_text(f"🗑 Канал @{target_ch} удален из списка источников.")

    def _save_channels_to_yaml(self):
        yaml_path = Path(__file__).parent.parent / "config.yaml"
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        data["source_channels"] = [
            {"username": c.username, "title": c.title, "original_url": c.original_url}
            for c in config.source_channels
        ]

        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    async def cmd_digest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update):
            return

        msg = await update.message.reply_text("⏳ Генерирую еженедельный дайджест лучших новостей...")
        try:
            await promo.post_digest()
            await msg.edit_text("🔥 <b>Дайджест успешно опубликован в @WrongDevp!</b>", parse_mode="HTML")
        except Exception as e:
            await msg.edit_text(f"❌ Ошибка публикации дайджеста: {e}")

    async def cmd_analytics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update):
            return

        msg = await update.message.reply_text("⏳ Считываю охваты и реакции из @WrongDevp, анализирую эффективность тем...")
        try:
            report = await analytics.generate_telegram_report()
            await msg.edit_text(report, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            logger.error(f"Error generating analytics report: {e}", exc_info=True)
            await msg.edit_text(f"❌ Ошибка формирования аналитики: {e}")

    async def handle_menu_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_auth(update):
            return

        text = update.message.text.strip()
        if "📊 Статус" in text:
            await self.cmd_status(update, context)
        elif "🔄 Проверить сейчас" in text:
            await self.cmd_check(update, context)
        elif "⏸ Пауза" in text or "▶️ Пуск" in text:
            if self.app.is_paused:
                await self.cmd_resume(update, context)
            else:
                await self.cmd_pause(update, context)
        elif "🧠 Анализ постов" in text or "Анализ" in text:
            await self.cmd_analytics(update, context)
        elif "📈 Статистика" in text:
            await self.cmd_stats(update, context)
        elif "📋 Список каналов" in text:
            await self.cmd_channels(update, context)
        elif "📰 Выпустить дайджест" in text:
            await self.cmd_digest(update, context)
        else:
            await self.cmd_help(update, context)