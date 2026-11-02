"""Telegram reports to the owner: publications, stuck work and a daily digest.

The 27 October refactor dropped the pipeline's own alert, so nothing told the owner
what the factory did. Every call here is best effort and silent when disabled: a failed
report must never block publication. It sends only when data/owner_notify.json says
{"enabled": true}, so tests and staging copies cannot message anyone.
"""
import html
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from dotenv import load_dotenv

BASE_DIR = Path(os.getenv('VIDEO_FACTORY_DIR', Path(__file__).resolve().parent.parent))
DATA = BASE_DIR / 'data'
SETTINGS = DATA / 'owner_notify.json'
STATE = DATA / 'owner_notify_state.json'
AUTOPOSTER_DB = Path('/home/gut/hermes-workspace/tg_autoposter/data/tg_autoposter.db')
MSK = ZoneInfo('Europe/Moscow')
LABELS = {'wrongdev': 'WrongDev', 'migratoria': 'Мигратория', 'lvl': 'LVL'}
PLATFORMS = {'youtube': 'YouTube', 'telegram': 'Telegram', 'vk': 'VK', 'reddit': 'Reddit', 'instagram': 'Instagram',
             'tiktok': 'TikTok', 'dzen': 'Дзен', 'x': 'X'}
EVENTS = DATA / 'publication_events.json'
LOG = DATA / 'publication_log.jsonl'
SECRET = re.compile(r'(bot\d{6,}:[A-Za-z0-9_-]{20,}|access_token=[^&\s]+|\b\d{8,}:[A-Za-z0-9_-]{30,}\b)')
PROXY_URL = os.getenv('HTTP_PROXY', 'http://127.0.0.1:17890')
OUTBOX_LIMIT = 20


def _read(path, default):
    try:return json.loads(Path(path).read_text())
    except (OSError, ValueError):return default


def _write(path, value):
    tmp = Path(path).with_name(f'{Path(path).name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False))
    tmp.replace(path)


def enabled():
    return bool(_read(SETTINGS, {}).get('enabled'))


def _deliver(text):
    load_dotenv(Path.home() / '.hermes/.env', override=False)
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat = (os.getenv('TELEGRAM_ALLOWED_USERS') or '').split(',')[0].strip()
    if not token or not chat:return False
    try:
        r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage', timeout=12,
            json={'chat_id': chat, 'text': text[:4000], 'parse_mode': 'HTML', 'disable_web_page_preview': True},
            proxies={'http': PROXY_URL, 'https': PROXY_URL})
        return r.status_code == 200 and r.json().get('ok') is True
    except (requests.RequestException, ValueError):return False


def send(text, key=None, cooldown=0):
    """Deliver now, or keep it for the next call. key+cooldown drops repeats of one condition.

    Best effort by contract: a report problem must never reach the publisher that called it,
    and an unreachable Telegram costs one short attempt, not one per queued message."""
    try:
        if not enabled():return False
        state = _read(STATE, {'sent': {}, 'outbox': []})
        now = time.time()
        if key and now - state['sent'].get(key, 0) < cooldown:return False
        queue, delivered = state['outbox'] + [text], 0
        while delivered < len(queue) and _deliver(queue[delivered]):delivered += 1
        state['outbox'] = queue[delivered:][-OUTBOX_LIMIT:]
        sent = delivered == len(queue)
        if key and sent:state['sent'][key] = now
        state['sent'] = {k: v for k, v in state['sent'].items() if now - v < 7 * 86400}
        _write(STATE, state)
        return sent
    except Exception:return False


def label(brand):
    cfg = _read(DATA / 'media_brands.json', {}).get(brand, {})
    return cfg.get('label') or LABELS.get(brand, brand)


def platform_name(platform):
    return PLATFORMS.get(platform, platform)


def clean(text, limit=220):
    """One short, secret-free line from an error or traceback tail."""
    text = SECRET.sub('<hidden>', str(text or ''))
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    useful = [l for l in lines if not l.startswith(('File "', 'Traceback', '^', '~'))] or lines
    return ' '.join(useful[-2:])[:limit] if useful else ''


def clock(epoch):
    return datetime.fromtimestamp(epoch, MSK).strftime('%H:%M')


def published(brand, title, url, next_due):
    publication(brand, 'youtube', 'video', title, url, announce=False)  # announced right now, only logged here
    send(f'🎬 <b>{label(brand)}</b>: видео опубликовано\n{html.escape(title)}\n{html.escape(url)}\n'
         f'Следующий слот: {clock(next_due)} МСК', key='published:' + url, cooldown=86400)


def publication(brand, platform, kind, title, url, lang=None, announce=True):
    """Record one confirmed publication on any platform. It is logged forever (the analytics use the log) and
    sent in the next roll-up, so a six-platform video costs one message instead of six."""
    try:
        entry = {'t': round(time.time()), 'brand': brand, 'platform': platform, 'kind': kind,
                 'title': (title or '')[:120], 'url': url, 'lang': lang}
        known = {json.loads(l).get('url') for l in LOG.read_text().splitlines()[-3000:] if l.strip()} if LOG.exists() else set()
        if url in known:return False
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a') as stream:stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
        if announce:
            pending = _read(EVENTS, [])
            pending.append(entry)
            _write(EVENTS, pending[-200:])
        return True
    except Exception:return False


def failure(brand, platform, reason, cooldown=6 * 3600):
    """Immediate alert for a platform that failed to publish; one per brand and platform per cooldown."""
    reason = clean(reason)
    login = bool(re.search(r'NeedsLogin|log ?in|sign ?in|session|authoriz|вход', reason, re.I))
    head = '🔐 нужен вход в аккаунт' if login else '❌ не удалось опубликовать'
    send(f'{head}\n<b>{html.escape(label(brand))}</b> · {html.escape(platform_name(platform))}\n{html.escape(reason or "причина не записана")}',
         key=f'fail:{brand}:{platform}', cooldown=cooldown)


def retrying(brand, attempts, error):
    """The same slot has failed three times in a row (not a review hold): tell the owner once per four hours."""
    if attempts >= 3:
        send(f'🔁 <b>{html.escape(label(brand))}</b>: видео не выходит, {attempts} попыток подряд\n{html.escape(clean(error))}',
             key=f'retry:{brand}', cooldown=4 * 3600)


PROBLEM_TEXT = {
    'platform_moderation_hold': ('🚫', 'площадка остановила публикации (модерация)', 6 * 3600),
    'photo_publication_unverified': ('⚠️', 'публикация фото не подтверждена на площадке', 6 * 3600),
    'browser_unavailable': ('🖥', 'браузер завода недоступен', 3 * 3600),
    'media_network_unavailable': ('🌐', 'нет рабочего маршрута для площадок', 3 * 3600),
    'media_network_check_failed': ('🌐', 'проверка сети завершилась ошибкой', 3 * 3600),
    'unit_was_inactive': ('🛠', 'служба была остановлена, запуск запрошен', 3 * 3600),
    'analytics_stale': ('📉', 'аналитика не обновлялась больше двух часов', 6 * 3600),
    'research_tasks_stale': ('⏱', 'фоновые задачи исследования зависли', 6 * 3600),
    'disk_low': ('💾', 'мало места на диске сервера', 12 * 3600),
}


def problems(items):
    """Owner alerts for health problems that need attention. Routine retries stay silent."""
    for item in items:
        issue = item.get('issue', '')
        if issue.endswith('_publication_unverified') and issue != 'photo_publication_unverified':
            name = issue.removesuffix('_publication_unverified')
            send(f'⚠️ <b>{html.escape(platform_name(name))}</b>: публикация не подтверждена на странице аккаунта\n'
                 f'{html.escape(str(item.get("source_id") or ""))} {html.escape(clean(item.get("reason")))}'.strip(),
                 key=f'problem:{issue}:{item.get("source_id")}', cooldown=6 * 3600)
        elif issue == 'browser_attachment_unavailable' and item.get('consecutive_failures', 0) >= 3:
            send('🖥 Браузер завода не отвечает уже несколько проверок подряд', key='problem:browser_attachment', cooldown=3 * 3600)
        elif issue in PROBLEM_TEXT:
            icon, text, cooldown = PROBLEM_TEXT[issue]
            who = ' · '.join(x for x in (label(item['brand']) if item.get('brand') else '', platform_name(item['platform']) if item.get('platform') else '',
                                         item.get('unit') or '') if x)
            send(f'{icon} {html.escape(text)}' + (f'\n{html.escape(who)}' if who else '') +
                 (f'\n{html.escape(clean(item.get("reason") or item.get("recovery") or ""))}' if item.get('reason') or item.get('recovery') else ''),
                 key=f'problem:{issue}:{item.get("brand")}:{item.get("platform")}:{item.get("unit")}', cooldown=cooldown)


def _channel_events():
    """WrongDev's own Telegram posts are made by the autoposter service, not by the distributor: poll its database."""
    state = _read(STATE, {'sent': {}, 'outbox': []})
    last = state.get('tg_last_id')
    try:
        with sqlite3.connect(f'file:{AUTOPOSTER_DB}?mode=ro', uri=True) as con:
            top = con.execute("SELECT max(target_msg_id) FROM posts WHERE status='posted'").fetchone()[0] or 0
            if last is None:
                state['tg_last_id'] = top
                _write(STATE, state)
                return 0
            rows = con.execute("SELECT title, target_msg_id FROM posts WHERE status='posted' AND target_msg_id>? ORDER BY target_msg_id", (last,)).fetchall()
    except sqlite3.Error:return 0
    for title, message_id in rows:
        publication('wrongdev', 'telegram', 'post', title, f'https://t.me/WrongDevp/{message_id}')
    if rows:
        state = _read(STATE, {'sent': {}, 'outbox': []})
        state['tg_last_id'] = max(r[1] for r in rows)
        _write(STATE, state)
    return len(rows)


def flush_events(force=False):
    """Send the roll-up of confirmed publications once it is 15 minutes old or has six entries."""
    try:
        if not enabled():return False
        _channel_events()
        pending = _read(EVENTS, [])
        if not pending:return False
        if not force and len(pending) < 6 and time.time() - pending[0]['t'] < 900:return False
        lines = [f'📣 <b>Опубликовано ({len(pending)})</b>']
        brands = list(dict.fromkeys(e['brand'] for e in pending))
        for brand in brands:
            lines.append(f'<b>{html.escape(label(brand))}</b>')
            for e in (x for x in pending if x['brand'] == brand):
                kind = {'video': 'видео', 'post': 'пост', 'article': 'статья'}.get(e.get('kind'), e.get('kind') or '')
                lang = f" [{e['lang'].upper()}]" if e.get('lang') else ''
                lines.append(f"• {html.escape(platform_name(e['platform']))} {kind}{lang}: {html.escape(e.get('title') or '')[:70]}\n  {html.escape(e['url'])}")
        text = '\n'.join(lines)
        while len(text) > 3900 and len(lines) > 3:
            lines.pop();text = '\n'.join(lines) + '\n…'
        if send(text):
            _write(EVENTS, [])
            return True
        return False
    except Exception:return False


def held(brand, run_id, reason):
    send(f'⚠️ <b>{label(brand)}</b>: видео не вышло, источник отложен и взят следующий\n'
         f'{html.escape(reason[:300])}\nПрогон: {html.escape(run_id)}', key='held:' + run_id, cooldown=7 * 86400)


def overdue(problems):
    """One reminder per brand every four hours while a slot stays late by more than two hours."""
    for item in problems:
        if item.get('issue') == 'publication_overdue' and item.get('seconds', 0) > 7200:
            send(f"🔴 <b>{label(item['brand'])}</b>: публикация просрочена на {item['seconds'] // 3600} ч\n"
                 f"{html.escape(str(item.get('error') or 'причина не записана')[:300])}",
                 key='overdue:' + item['brand'], cooldown=4 * 3600)


def _published_runs(since):
    runs = []
    for path in (BASE_DIR / 'output/runs').glob('*/manifest.json'):
        try:
            if path.stat().st_mtime < since:continue
            m = json.loads(path.read_text())
        except (OSError, ValueError):continue
        if m.get('status') == 'published':
            runs.append({'brand': m.get('brand', 'wrongdev'), 'title': m.get('title') or '', 'url': m.get('youtube_url') or '',
                         'at': path.stat().st_mtime})
    return sorted(runs, key=lambda r: r['at'])


def _channel_posts(since):
    counts = {'wrongdev': 0, 'migratoria': 0}
    try:
        with sqlite3.connect(f'file:{AUTOPOSTER_DB}?mode=ro', uri=True) as con:
            cutoff = datetime.fromtimestamp(since, timezone.utc).replace(tzinfo=None).isoformat()
            counts['wrongdev'] = con.execute("SELECT count(*) FROM posts WHERE status='posted' AND posted_date>?", (cutoff,)).fetchone()[0]
        with sqlite3.connect(f"file:{DATA / 'migratoria.db'}?mode=ro", uri=True) as con:
            counts['migratoria'] = con.execute('SELECT count(*) FROM posts WHERE telegram_id IS NOT NULL AND created_at>?', (since,)).fetchone()[0]
    except sqlite3.Error:return None
    return counts


def _platform_block(since):
    """Confirmed publications per network and account in the period, from the owner publication log."""
    counts = {}
    try:
        for line in LOG.read_text().splitlines():
            try:row = json.loads(line)
            except ValueError:continue
            if row.get('t', 0) >= since:
                by_platform = counts.setdefault(row['brand'], {})
                by_platform[row['platform']] = by_platform.get(row['platform'], 0) + 1
    except OSError:return []
    if not counts:return ['', '<b>Публикации по площадкам</b>', '• за период подтверждённых публикаций нет']
    out = ['', '<b>Публикации по площадкам</b>']
    for brand in LABELS:
        if brand in counts:out.append(f"• {label(brand)}: " + ', '.join(f'{platform_name(p)} {n}' for p, n in sorted(counts[brand].items())))
    return out


def _growth_block(feedback, since):
    """Subscribers per channel (change over the period) and the measured diagnosis headline per brand."""
    out = []
    channels = feedback.get('channels') or {}
    series = {}
    try:
        for line in (DATA / 'metrics_series.jsonl').read_text().splitlines():
            try:row = json.loads(line)
            except ValueError:continue
            if str(row.get('k', '')).startswith('channel:') and row.get('subscribers') is not None:series.setdefault(row['k'][8:], []).append(row)
    except OSError:pass
    brands = _read(DATA / 'media_brands.json', {})
    for brand, cfg in brands.items():
        cid = cfg.get('youtube_channel_id')
        if not cid or cid not in channels:continue
        now_subs = channels[cid]['subscribers']
        before = [r for r in series.get(cid, []) if r['t'] <= since]
        delta = f" ({now_subs - before[-1]['subscribers']:+d} за период)" if before else ''
        out.append(f"• {label(brand)}: YouTube {now_subs} подписчиков{delta}, {channels[cid]['views']} просмотров канала")
    try:
        import growth_diagnosis
        out += ['• ' + html.escape(x) for x in growth_diagnosis.digest_lines(label=label)]
    except Exception:pass
    try:
        import growth_engine
        out += ['• ' + html.escape(x) for x in growth_engine.digest_lines(label=label)]
    except Exception:pass
    return ['', '<b>Рост</b>'] + out if out else []


def digest(hours=24):
    now = time.time()
    since = now - hours * 3600
    schedule, health = _read(DATA / 'media_schedule.json', {}), _read(DATA / 'media_health.json', {})
    feedback, held_sources = _read(DATA / 'content_feedback.json', {}), _read(DATA / 'review_held_video_sources.json', {})
    runs, posts = _published_runs(since), _channel_posts(since)
    lines = [f'📊 <b>Итоги за {hours} ч</b> · {datetime.fromtimestamp(now, MSK).strftime("%d.%m %H:%M")} МСК', '', '<b>Видео</b>']
    configured = _read(DATA / 'media_brands.json', {})
    for brand in LABELS:
        if brand in configured and not (configured[brand].get('video_enabled') or configured[brand].get('lvl_enabled')):continue
        mine = [r for r in runs if r['brand'] == brand]
        slot = schedule.get(brand, {})
        late = now - slot.get('next_due', now)
        status = f"слот просрочен на {int(late // 3600)} ч" if late > 1800 else f"следующий слот {clock(slot['next_due'])}" if slot.get('next_due') else ''
        lines.append(f'• {label(brand)}: {len(mine)} опубликовано' + (f' · {status}' if status else ''))
        lines += [f"   {clock(r['at'])} — {html.escape(r['title'][:70])} {html.escape(r['url'])}" for r in mine]
    lines += ['', '<b>Посты в Telegram</b>']
    lines += ([f'• {label(b)}: {n}' for b, n in posts.items()] if posts else ['• данные недоступны'])
    problems = [p for p in health.get('problems', []) if p.get('issue') != 'research_tasks_retrying']
    lines += ['', '<b>Состояние</b>', '• ' + ('замечаний нет' if not problems else f'проблем: {len(problems)}')]
    for p in problems[:5]:
        lines.append('   — ' + html.escape(f"{label(p['brand']) + ': ' if p.get('brand') else ''}{p['issue']}" + (f" ({str(p['error'])[:90]})" if p.get('error') else '')))
    waiting = sum(len(v) for v in held_sources.values())
    if waiting:lines.append(f'• отложено источников на ручную проверку: {waiting}')
    lines += _platform_block(since) + _growth_block(feedback, since)
    top = []
    for brand, info in feedback.get('brands', {}).items():
        for group in info.get('same_age_comparisons', []):
            if group.get('platform') == 'youtube' and group.get('top'):
                top.append((group['top'][0].get('views') or 0, brand, group['top'][0].get('title') or ''))
    if top:
        views, brand, title = max(top)
        lines += ['', f'📈 Лучшее видео по сопоставимому возрасту: {label(brand)} — «{html.escape(title[:70])}», {views} просмотров']
    return '\n'.join(lines)


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else 'digest'
    if command == 'digest':
        text = digest(int(sys.argv[2]) if len(sys.argv) > 2 else 24)
        if '--dry' in sys.argv:print(text)
        else:print('sent' if send(text) else 'not sent (disabled or Telegram unreachable; kept for retry)')
    else:sys.exit('usage: owner_notify.py digest [hours] [--dry]')
