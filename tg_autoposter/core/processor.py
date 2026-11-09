import json
import re
import os
import subprocess
import logging
from typing import Optional, Dict, Any, Tuple
import httpx
from openai import OpenAI

from .config import config
from .dedup import dedup
from .editorial_policy import allowed, EDITORIAL_SCOPE
from .storage import storage

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — автор и технический редактор Telegram-канала WrongDev (@WrongDevp).
Аудитория канала — программисты, ML-инженеры и техно-энтузиасты (вайбкодинг, AI-инструменты, полезный софт, локальные LLM, AI-халява).

Твоя задача — переписать пост из стороннего канала в живой, ёмкий авторский пост БЕЗ НЕЙРОСЛОПА (AI-slop).

1. ФИЛЬТРАЦИЯ РЕКЛАМЫ И AI-ХАЛЯВА (КРИТИЧЕСКИ ВАЖНО):
А) ЧТО СЧИТАТЬ РЕКЛАМОЙ (БЛОКИРОВАТЬ И ПРОПУСКАТЬ ПОСТ):
- Платные сервисы, продажи аккаунтов, боты магазинов цифровых товаров, платные VPN, казино, крипто-сигналы, скам, платные курсы.
-> верни строго JSON: {"skip": true, "reason": "реклама"}

Б) ЧТО НЕ ЯВЛЯЕТСЯ РЕКЛАМОЙ (ЭТО КОРНЕВОЙ ПРОФИЛЬ КАНАЛА, ПУБЛИКОВАТЬ ОБЯЗАТЕЛЬНО):
- AI-ХАЛЯВА И БЕСПЛАТНЫЙ ДОСТУП: раздачи бесплатных API-токенов, баги/абузы тарифных планов, рабочие промокоды на бесплатный доступ к моделям (Claude, GPT, DeepSeek и др.), триалы без привязки карт, бесплатные шлюзы (например, получение 200M бесплатных токенов на oxionai или других сервисах без оплаты).
- Это САМЫЙ ЦЕННЫЙ контент для подписчиков WrongDev! Такие посты ОБЯЗАТЕЛЬНО одобрять (skip: false).
- При переработке: полностью удали рекламу чужих Telegram-каналов («подпишитесь на наш канал»), реферальные хвосты (?r=..., utm_...), но ОБЯЗАТЕЛЬНО СОХРАНИ рабочий алгоритм получения халявы (ссылку на сервис, промокод, шаги активации) и перепиши в бодром авторском стиле разработчика!

2. СТРОГИЕ ПРАВИЛА ПРОТИВ НЕЙРОСЛОПА (AI-SLOP):
- ЗАПРЕЩЕНЫ искусственные подзаголовки: НИКАКИХ «Что внутри:», «В чём суть:», «Хардварные реалии:», «Железо и ресурсы:», «Быстрый старт:», «Инсайт:», «Итог:», «Вывод:». Текст должен читаться как единая связная авторская заметка, а не как школьный реферат или карточка товара.
- ЗАПРЕЩЕНО слово «Инсайт» в любом виде. Если есть технический нюанс — просто расскажи его как факт или наблюдение.
- ЗАПРЕЩЕНЫ маркированные списки с определениями терминов вроде «• SFT — это... • GRPO — это...». Читатели — разработчики, они знают термины. Объясняй только суть проекта и пользу.
- ЗАПРЕЩЕНЫ нейро-штампы и канцелярит: «выкатила», «представлен», «революционный», «настоящий геймчейнджер», «открывает новые горизонты», «стоит отметить», «не просто инструмент, а...», «давайте разберемся», «в сухом остатке», «под капотом скрывается», «пошаговое руководство», «не упустите возможность».
- ЗАПРЕЩЕНЫ эмодзи-гирлянды (🚀, 🔥, ⚡️, 🧠, 💡, 📌 перед каждой строкой). Максимум 1-2 эмодзи на весь пост (например, 👉 перед ссылкой), либо вообще без них.
- ЗАПРЕЩЕНО дублировать заголовок первым предложением.

3. КАК ПИСАТЬ (ЖИВОЙ АВТОРСКИЙ СТИЛЬ):
- Пиши на живом, уверенном русском языке разработчика. Короткие ёмкие фразы, естественные переходы.
- Структура:
  1) Жирный заголовок в первой строке: <b>Суть новости/инструмента простыми словами</b>.
  2) 2-4 плотных предложения: что это, чем круто, как устроено, практические детали (сколько VRAM жрёт, на чём написано, чистый ли код).
  3) Если уместно — одна короткая строка запуска или команды в теге <code>...</code>.
  4) Прямая ссылка на GitHub / источник: «👉 <a href="...">GitHub</a>» или ссылка вшита в текст.
  5) Подпись в самом конце: \\n\\n<a href="https://t.me/WrongDevp">WrongDev | AI & Dev</a>

4. ЛИМИТ ДЛИНЫ (КРИТИЧНО):
- Длина всего поля "text" СТРОГО от 400 до 850 символов (максимум 950 с учетом HTML-тегов). Пост ДОЛЖЕН целиком помещаться в подпись к фото Telegram (лимит 1024), чтобы не разрываться на два сообщения!

5. ТЕГИ HTML TELEGRAM:
- Используй только <b>, <i>, <code>, <pre>, <blockquote>, <a href="...">.
- НЕ используй <br>, <p>, <ul>, <li>. Переносы строк — через обычные \\n.

ФОРМАТ ОТВЕТА СТРОГО JSON:
{
  "skip": false,
  "key_topic": "краткая тема 2-4 слова на англ/рус для дедупликации",
  "title": "Краткий заголовок без тегов",
  "text": "Готовый живой HTML-текст"
}
"""


SYSTEM_PROMPT += "\nНе добавляй подпись Источник или Source внизу поста. Полезные ссылки на сервис или репозиторий оставляй естественно в тексте. Политика тем владельца имеет приоритет над одобрением любой AI-новости.\n" + EDITORIAL_SCOPE


from contextvars import ContextVar
growth_post_receipt = ContextVar('growth_post_receipt', default={})


def audience_instructions():
    import sys
    sys.path.insert(0, '/home/gut/hermes-workspace/video_factory/scripts')
    from content_feedback import _feedback_brief
    from growth_actions import context
    editorial = context('wrongdev', platform='telegram', kind='post')
    growth_post_receipt.set(editorial['receipt'])
    return _feedback_brief('wrongdev') + editorial['prompt']



def strip_source_footer(text):
    return re.sub(r"(?im)^\s*(?:<[^>]+>)*(?:Источники?|Source)(?:</[^>]+>)*\s*:\s*[^\n]*(?:\n|$)", "", text).strip()


def extract_json(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    raw = raw.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    try:
        return json.loads(raw)
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start:end+1])
        except Exception:
            pass
    return None


class PostProcessor:
    def __init__(self):
        self.groq_client: Optional[OpenAI] = None
        self.openrouter_client: Optional[OpenAI] = None
        self._init_clients()

    def _init_clients(self):
        # OpenRouter для Nemotron
        openrouter_key = os.getenv(
            "HERMES_OPENROUTER_FREE_API_KEY",
            ""
        )
        try:
            http_client = httpx.Client(proxy="http://127.0.0.1:17890", timeout=35.0)
            self.openrouter_client = OpenAI(
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1",
                http_client=http_client
            )
        except Exception as e:
            logger.warning(f"OpenRouter client init warning: {e}")

        # Groq (Qwen 27B)
        groq_key = os.getenv("GROQ_API_KEY", config.llm.api_key)
        try:
            http_client_groq = httpx.Client(proxy="http://127.0.0.1:17890", timeout=35.0)
            self.groq_client = OpenAI(
                api_key=groq_key,
                base_url="https://api.groq.com/openai/v1",
                http_client=http_client_groq
            )
        except Exception as e:
            logger.warning(f"Groq client init warning: {e}")

    def clean_html(self, html: str, title: str = "") -> str:
        """Приводит HTML к строго поддерживаемому Telegram Bot API формату и обеспечивает структуру."""
        html = strip_source_footer(html)
        html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        html = re.sub(r"</p>\s*<p>", "\n\n", html, flags=re.IGNORECASE)
        html = re.sub(r"</?p>", "\n", html, flags=re.IGNORECASE)
        html = re.sub(r"<li[^>]*>", "• ", html, flags=re.IGNORECASE)
        html = re.sub(r"</li>", "\n", html, flags=re.IGNORECASE)
        html = re.sub(r"</?[uo]l[^>]*>", "\n", html, flags=re.IGNORECASE)
        html = re.sub(r"\n{3,}", "\n\n", html).strip()

        # Если заголовок не в начале, добавляем жирный заголовок
        if title and not html.startswith("<b>"):
            html = f"<b>{title}</b>\n\n{html}"

        # Проверяем наличие подписи канала
        signature = '<a href="https://t.me/WrongDevp">WrongDev | AI & Dev</a>'
        if signature not in html and "@WrongDevp" not in html:
            html = f"{html}\n\n{signature}"

        # Удаляем дублирующиеся подписи, если они возникли
        parts = html.split(signature)
        if len(parts) > 2:
            html = signature.join(parts[:-1]).rstrip() + "\n\n" + signature

        return html.strip()

    def heuristic_ad_check(self, text: str) -> Tuple[bool, str]:
        # Сначала проверяем наличие технического контента — если пост содержательный,
        # не блокируем его по промокоду/рефке (LLM сам разберётся).
        tech_indicators = [
            r"(?i)(cursor|claude code|cline|opencode|vscode|github|gitlab)",
            r"(?i)(llm|model|token|context|vram|api|sdk|cli)",
            r"(?i)(fable|opus|sonnet|haiku|gpt|gemini|luna|deepseek|qwen|llama|mistral)",
            r"(?i)(ml|machine learning|fine.?tun|train|infer|bench|eval)",
            r"(?i)(python|rust|go|javascript|typescript|docker|kubernetes)",
            r"(?i)(github\.com|huggingface\.co|arxiv\.org|pypi\.org)",
            r"(?i)(\d+\s*(млн|м|k|tokens?|context|vram|gb|gb))",
            r"(?i)(запуск|установк|инсталл|команд|скрипт|config|настройк)",
        ]
        has_tech_content = any(re.search(p, text) for p in tech_indicators)
        
        patterns = [
            (r"(?i)(купить|продажа|аккаунты|подписк\w+).*?(gemini|chatgpt|claude|midjourney|spotify|netflix|youtube premium).*?(\d+\s*(руб|₽|\$)|скидк|дешев|боте|магазин)", "account_store"),
            (r"(?i)(тайник\s*vpn|vpntaynik|тайник\s*store|купить\s*vpn|лучший\s*vpn)", "ad_vpn_store"),
            (r"(?i)(ставки|казино|1win|сигналы на крипту|пассивный доход от \d+%|раскрутка депозита)", "gambling_crypto_scam"),
            # Промокод — флаг только если пост короткий ИЛИ нет технического контента
            (r"(?i)(промокод|промо-код)\s*[:=]\s*[a-z0-9_-]+", "promo_code_ad"),
            (r"(?i)(купите|скидк\w+\s*\d+%).*?(@\w+bot|t\.me/\w+bot)", "bot_sales_ad"),
        ]
        for pat, reason in patterns:
            if re.search(pat, text):
                # Для промокодов: пропускаем эвристику, если есть технический контент
                if reason == "promo_code_ad" and has_tech_content:
                    continue
                return True, reason
        return False, ""

    def _call_gemini_cli(self, raw_text: str) -> Optional[Dict[str, Any]]:
        """1-й приоритет: Умная модель Gemini 3.8 Flash High через официальный Antigravity CLI."""
        prompt = f"{SYSTEM_PROMPT}{audience_instructions()}\n\nПОСТ ДЛЯ ОБРАБОТКИ:\n{raw_text[:4000]}"
        cmd = [
            "/home/gut/.local/bin/agy",
            "-p", prompt,
            "--model", "gemini-3.8-flash-high",
            "--effort", "high",
            "--output-format", "json"
        ]
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/home/gut",
            "http_proxy": "http://127.0.0.1:17892",
            "https_proxy": "http://127.0.0.1:17892"
        }
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=80)
            if res.returncode == 0 and res.stdout:
                outer = json.loads(res.stdout)
                if outer.get("status") == "SUCCESS":
                    response_text = outer.get("response", "")
                    parsed = extract_json(response_text)
                    if parsed:
                        logger.info("Successfully processed with Gemini 3.8 Flash High (AI Pro)")
                        return parsed
            logger.warning(f"Gemini CLI returncode={res.returncode}, stderr={res.stderr[:200]}")
        except Exception as e:
            logger.warning(f"Gemini CLI call failed: {e}")
        return None

    def _call_nemotron(self, raw_text: str) -> Optional[Dict[str, Any]]:
        """2-й приоритет: NVIDIA Nemotron 3 Ultra через OpenRouter."""
        if not self.openrouter_client:
            return None

        models_to_try = [
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3.5-super-vl:free"
        ]

        for model_name in models_to_try:
            try:
                resp = self.openrouter_client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT + audience_instructions()},
                        {"role": "user", "content": raw_text[:4000]}
                    ],
                    temperature=0.3,
                    response_format={"type": "json_object"}
                )
                content = resp.choices[0].message.content
                parsed = extract_json(content)
                if parsed:
                    logger.info(f"Successfully processed with Nemotron ({model_name})")
                    return parsed
            except Exception as e:
                logger.warning(f"Nemotron ({model_name}) error: {e}")
        return None

    def _call_groq_fallback(self, raw_text: str) -> Optional[Dict[str, Any]]:
        """3-й приоритет: Резерв Groq (Qwen 27B)."""
        if not self.groq_client:
            return None

        for model_name in ["qwen/qwen3.8-27b"]:
            try:
                resp = self.groq_client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT + audience_instructions()},
                        {"role": "user", "content": raw_text[:4000]}
                    ],
                    temperature=0.3,
                    response_format={"type": "json_object"}
                )
                content = resp.choices[0].message.content
                parsed = extract_json(content)
                if parsed:
                    logger.info(f"Successfully processed with Groq fallback ({model_name})")
                    return parsed
            except Exception as e:
                logger.warning(f"Groq ({model_name}) error: {e}")
        return None

    def process(self, raw_text: str, source_channel: str, source_msg_id: int) -> Dict[str, Any]:
        """Полный цикл валидации, фильтрации и умного рерайта."""
        if not allowed(raw_text):
            return {"status": "skip_policy", "reason": "owner_topic_policy"}
        # 1. Быстрая проверка на длину
        if len(raw_text.strip()) < 60:
            return {"status": "skip_short", "reason": "text_too_short"}

        # 2. Быстрый regex-фильтр спама
        is_ad, ad_reason = self.heuristic_ad_check(raw_text)
        if is_ad:
            return {"status": "skip_ad", "reason": f"heuristic_{ad_reason}"}

        # 3. Дедупликация сырого текста
        is_dup, dup_reason = dedup.is_duplicate(raw_text)
        if is_dup:
            return {"status": "skip_dup", "reason": dup_reason}

        # 4. Вызов моделей в порядке приоритета: Gemini -> Nemotron -> Groq
        data = self._call_gemini_cli(raw_text)
        provider_name = "Gemini 3.8 Flash High"

        if not data:
            logger.info("Gemini unavailable, attempting Nemotron (OpenRouter)...")
            data = self._call_nemotron(raw_text)
            provider_name = "Nemotron (OpenRouter)"

        if not data:
            logger.info("Nemotron unavailable, falling back to Groq...")
            data = self._call_groq_fallback(raw_text)
            provider_name = "Groq (Qwen 27B)"

        if not data:
            return {"status": "error", "reason": "all_models_failed"}

        # 5. Проверка решения LLM
        if data.get("skip"):
            return {"status": "skip_ad", "reason": data.get("reason", "llm_ad_rejection")}

        key_topic = data.get("key_topic", "")
        title = data.get("title", "")
        raw_post_text = data.get("text", "")
        formatted_html = self.clean_html(raw_post_text, title=title)

        # 6. Вторичная проверка дедупликации по теме
        is_topic_dup, topic_reason = dedup.is_duplicate(raw_text, key_topic=key_topic, title=title)
        if is_topic_dup:
            return {"status": "skip_dup", "reason": topic_reason}

        return {
            "status": "ok",
            "key_topic": key_topic,
            "title": title,
            "html": formatted_html,
            "original_text": raw_text,
            "provider": provider_name,
            "growth_actions": growth_post_receipt.get(),
        }


processor = PostProcessor()
