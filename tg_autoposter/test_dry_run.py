import os
import asyncio
import json
import re
import httpx
from bs4 import BeautifulSoup
from openai import OpenAI

proxy = "http://127.0.0.1:17890"

client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
    http_client=httpx.Client(proxy=proxy)
)

system_prompt = """Ты — технический редактор Telegram-канала WrongDev (@WrongDevp) про AI-халяву, новинки в IT и обзоры инструментов.
Твоя задача — адаптировать пост из стороннего канала для публикации в нашем канале.

ПРАВИЛА ОТБОРА:
1. Если пост — РЕКЛАМА (продажа аккаунтов, магазин подписок, VPN, платные курсы, крипто-сигналы, скам, реферальные ссылки) -> ответь строго JSON:
   {"skip": true, "reason": "реклама"}
2. Если пост полезный:
   - Полностью вырежи рекламу, промокоды, партнерские хвосты ссылок (?r=..., ?start=..., utm_...), ссылки на чужие Telegram-каналы.
   - Обязательно СОХРАНИ ссылки на первоисточники: GitHub, Hugging Face, arXiv, официальные страницы инструментов.
   - Перепиши текст живо, емко, структурированно для разработчиков и AI-энтузиастов.
   - Используй HTML-разметку Telegram: <b>, <i>, <code>, <pre>, <a href="...">, <blockquote>. НЕ используй теги <br>, <ul>, <li>, <p> (переносы строк через обычные \n, маркеры списков через •).
   - Если уместно, добавь РЕАЛЬНО полезный технический инсайт/факт (VRAM требования, быстрая команда для CLI, скрытый нюанс архитектуры или аналог).
   - КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО: добавлять общие советы и банальности по безопасности ("делайте бэкапы", "помните о безопасности данных", "будьте осторожны"). Только техническая польза по существу. Если добавить нечего — не добавляй ничего лишнего.
   - В конце подпись: \n\n<a href="https://t.me/WrongDevp">WrongDev | AI & Dev</a>

Ответь СТРОГО валидным JSON:
{
  "skip": false,
  "key_topic": "краткая тема 2-4 слова (напр. DeepSeek V4.1)",
  "title": "Заголовок поста",
  "text": "Готовый HTML текст"
}
"""

def clean_html(html: str) -> str:
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</p>\s*<p>", "\n\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</?p>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<li[^>]*>", "• ", html, flags=re.IGNORECASE)
    html = re.sub(r"</li>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</?[uo]l[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()

async def test_channel(channel: str):
    url = f"https://t.me/s/{channel}"
    print(f"\n==================== CHANNEL: @{channel} ====================")
    async with httpx.AsyncClient(proxy=proxy, timeout=15) as http:
        r = await http.get(url)
    
    soup = BeautifulSoup(r.text, "html.parser")
    posts = soup.find_all("div", class_="tgme_widget_message")
    if not posts:
        print("No posts found")
        return

    # Возьмем самый последний содержательный пост
    target_post = None
    for p in reversed(posts):
        t_div = p.find("div", class_="tgme_widget_message_text")
        if t_div and len(t_div.text.strip()) > 80:
            target_post = p
            break
            
    if not target_post:
        print("No suitable text posts found")
        return

    post_id = target_post.get("data-post")
    text_div = target_post.find("div", class_="tgme_widget_message_text")
    raw_text = text_div.get_text(separator="\n").strip()
    
    print(f"Original Post ID: {post_id}")
    print(f"Original Length: {len(raw_text)} chars")
    print(f"Preview: {raw_text[:120].replace(chr(10), ' ')}...")
    
    # LLM processing
    try:
        resp = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_text}
            ],
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        data = json.loads(resp.choices[0].message.content)
        if data.get("skip"):
            print(f"==> RESULT: SKIPPED (Reason: {data.get('reason')})")
        else:
            print(f"==> RESULT: ACCEPTED!")
            print(f"    Topic: {data.get('key_topic')}")
            print(f"    Title: {data.get('title')}")
            cleaned_text = clean_html(data.get("text", ""))
            print(f"    Processed Text (preview):\n{cleaned_text[:300]}...\n")
    except Exception as e:
        print(f"LLM processing error: {e}")

async def main():
    channels = ["neuroprft", "vibecoding_tg", "ai4svoi"]
    for ch in channels:
        await test_channel(ch)

asyncio.run(main())
