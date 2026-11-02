"""The factory's analyst in code: what each video got, why, what to test next, and when a test is decided.

Runs after every analytics tick. It reads audience numbers through the YouTube Analytics API (no browser), joins them with
what each script actually did, explains each video by the funnel stage where it lost people, compares topics and script
choices, and closes randomised experiments on its own. Every statement carries its numbers and sample size.

What may change without a person: only script-writer choices inside a bounded experiment (growth_brief.py) and the order in
which ready sources become videos. Facts, topic policy, publishing and accounts are out of its reach. An unavailable number
stays absent; it is never treated as zero.
"""
import hashlib
import json
import math
import os
import random
import re
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(os.getenv('VIDEO_FACTORY_DIR', Path(__file__).resolve().parent.parent))
DATA = BASE_DIR / 'data'
REPORT = DATA / 'growth_report.json'
STORE = DATA / 'youtube_api_videos.json'
MSK = timezone(timedelta(hours=3))
API_INTERVAL = 6 * 3600     # Analytics API data moves slowly (48-72 h lag), one read per 6 h is plenty
MATURE_HOURS = 72           # same age basis for every comparison: the API has settled by then
SINCE = '2026-09-01'
METRICS = 'views,engagedViews,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained'
ADOPT_RATIO, REJECT_RATIO, DECISION_P = 1.25, 0.8, 0.2  # cheap, reversible choices: a one-sided error rate near 10 % is acceptable
MIN_CLASS = 3

# Topic classes: plain keyword lists, reviewable and easy to change. First match wins.
TOPIC_CLASSES = {
    'wrongdev': [('incident', r'уязв|бэкдор|взлом|украл|утечк|вредонос|атак|malware|backdoor|выдал|троллит|скандал|запрет'),
                 ('pricing_limits', r'лимит|тариф|\$|подписк|сброс|цен[аы]|бесплатн|ultra|pro\b'),
                 ('release', r'модел|релиз|выш[ла]|gpt|qwen|kimi|gemini|claude \d|llama|kerria|arena'),
                 ('howto', r'как |промпт|гайд|эконом|компонент|ide|правило|инструмент')],
    'migratoria': [('visa_border', r'виз[аыу]|въезд|безвиз|внж|вид на жит|граждан|границ|контрол|депорт|паспорт|миграц|подтвержден|транзит'),
                   ('loyalty', r'мил[ьи]|балл|hilton|marriott|ihg|bonvoy|honors|кэшбэк|карт'),
                   ('premium_curiosity', r'first class|первый класс|\bдуш\b|душ в|люкс|барбекю'),
                   ('route', r'рейс|s7|маршрут|аэропорт|авиакомпан|wi.?fi|starlink'),
                   ('life_abroad', r'жиль|недвиж|налог|банк|работ|пособ|кот|собак|переноск')],
}


def _load(path, default):
    try:return json.loads(Path(path).read_text())
    except (OSError, ValueError):return default


def _save(path, value):
    path = Path(path)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def channel_of(cfg):
    return cfg.get('youtube_channel_id') or cfg.get('publishing', {}).get('youtube', {}).get('channel_id')


def video_id(url):
    match = re.search(r'(?:youtu\.be/|shorts/|v=)([\w-]{11})', url or '')
    return match[1] if match else None


def topic_class(brand, text):
    low = (text or '').lower()
    for name, pattern in TOPIC_CLASSES.get(brand, []):
        if re.search(pattern, low):return name
    return 'other'


def collect_api(configs, now=None, fetch=None, token=None):
    """One bulk call per channel instead of one per video. Returns the store; an API failure keeps the old numbers."""
    now = now or time.time()
    store = _load(STORE, {'videos': {}, 'channels': {}, 'read_at': 0})
    if now - store.get('read_at', 0) < API_INTERVAL:return store
    import yt_analytics
    fetch = fetch or yt_analytics._get
    token = token or yt_analytics.access_token
    end = datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d')
    errors = {}
    for brand, cfg in configs.items():
        channel = channel_of(cfg)
        if not channel:continue
        try:
            access = token(channel, now)
            def report(**extra):
                return yt_analytics._rows(fetch(yt_analytics.REPORTS, {'ids': 'channel==MINE', 'startDate': SINCE, 'endDate': end, **extra}, access))
            for row in report(dimensions='video', metrics=METRICS, sort='-views', maxResults='200'):
                views = row.get('views') or 0
                store['videos'][row['video']] = {'brand': brand, 'views': views, 'engaged_views': row.get('engagedViews'),
                    'avg_duration_s': row.get('averageViewDuration'), 'avg_pct': row.get('averageViewPercentage'),
                    'likes': row.get('likes'), 'comments': row.get('comments'), 'shares': row.get('shares'), 'subs': row.get('subscribersGained'), 'read_at': now}
            info = {'read_at': now}
            info['traffic'] = {r['insightTrafficSourceType']: r['views'] for r in report(dimensions='insightTrafficSourceType', metrics='views')}
            info['countries'] = {r['country']: r['views'] for r in report(dimensions='country', metrics='views', sort='-views', maxResults='8')}
            info['age_gender'] = {r['ageGroup'] + ' ' + r['gender']: r['viewerPercentage'] for r in report(dimensions='ageGroup,gender', metrics='viewerPercentage')}
            store['channels'][brand] = info
        except Exception as exc:
            errors[brand] = type(exc).__name__ + ': ' + str(exc)[:200]
    store['read_at'] = now
    store['errors'] = errors
    _save(STORE, store)
    return store


def script_features(run_dir, brand):
    """What this video actually did, read from its script: nothing here is inferred from its performance."""
    try:script = json.loads((Path(run_dir) / 'script.json').read_text())
    except (OSError, ValueError):return {}
    scenes = script.get('scenes') or []
    spoken = [s.get('voiceover_ru', '') for s in scenes]
    total = sum(len(s.split()) for s in spoken)
    closing = len(spoken[-1].split()) if spoken else 0
    if script.get('closing') == 'short':closing = 4
    meta = script.get('youtube_meta') or {}
    return {'hook': spoken[0] if spoken else None, 'scenes': len(scenes), 'spoken_words': total, 'closing_share': round(closing / total, 2) if total else None,
        'closing': script.get('closing') or 'fixed', 'topic': topic_class(brand, meta.get('title', '') + ' ' + (spoken[0] if spoken else '')),
        'experiments': script.get('experiments') or {}, 'voice': script.get('narration_voice')}


def rows_for(brand, latest, store, now):
    rows = []
    for item in latest.get('items', []):
        if item.get('brand') != brand or item.get('platform') != 'youtube':continue
        vid = video_id(item.get('url'))
        public = item.get('metrics') or {}
        api = store.get('videos', {}).get(vid, {})
        published = item.get('published_at')
        row = {'title': (item.get('title') or '')[:90], 'url': item.get('url'), 'run_id': item.get('run_id'), 'duration': item.get('duration'),
            'age_hours': round((now - published) / 3600, 1) if published else None,
            'hour_msk': datetime.fromtimestamp(published, MSK).hour if published else None,
            'views': public.get('views') if public.get('views') is not None else api.get('views'),
            'likes': public.get('likes'), 'comments': public.get('comments'),
            'engaged_views': api.get('engaged_views'), 'avg_pct': api.get('avg_pct'), 'avg_duration_s': api.get('avg_duration_s'),
            'shares': api.get('shares'), 'subs': api.get('subs')}
        if row['views'] and row['engaged_views'] is not None:row['engaged_pct'] = round(100 * row['engaged_views'] / max(api.get('views') or row['views'], 1), 1)
        if item.get('run_id'):
            folder = BASE_DIR / 'output/runs' / item['run_id']
            if not folder.exists():folder = next(iter((BASE_DIR / 'output/lvl_runs').glob(item['run_id'] + '*')), folder)
            row.update(script_features(folder, brand))
        row.setdefault('topic', topic_class(brand, row['title']))
        rows.append(row)
    return rows


def per_thousand(row, key):
    views = row.get('views')
    return round(1000 * row[key] / views, 2) if views and row.get(key) is not None else None


METRIC_READERS = {
    'views': lambda r: r.get('views'),
    'engaged_views': lambda r: r.get('engaged_views'),
    'avg_pct': lambda r: r.get('avg_pct'),
    'engaged_pct': lambda r: r.get('engaged_pct'),
    'likes_per_1k': lambda r: per_thousand(r, 'likes'),
    'comments_per_1k': lambda r: per_thousand(r, 'comments'),
    'shares_per_1k': lambda r: per_thousand(r, 'shares'),
    'subs_per_1k': lambda r: per_thousand(r, 'subs'),
}


def diagnose(row, base):
    """Where this video lost people: one stage per finding, each naming the number and the comparison behind it."""
    if (row.get('age_hours') or 0) < 48:return [{'stage': 'too_early', 'evidence': f"{row.get('age_hours')} h old"}]
    out = []
    views, median = row.get('views'), base.get('median_views')
    if views is not None and median and views < 0.25 * median:
        out.append({'stage': 'not_distributed', 'evidence': f'{views} views against a brand median of {median:.0f}: the feed barely tested it'})
    if row.get('engaged_pct') is not None and base.get('median_engaged_pct') and row['engaged_pct'] < 0.8 * base['median_engaged_pct']:
        out.append({'stage': 'weak_opening', 'evidence': f"{row['engaged_pct']}% of views engaged against a median of {base['median_engaged_pct']}%", 'look_at': row.get('hook')})
    if row.get('avg_pct') is not None and base.get('median_avg_pct') and row['avg_pct'] < 0.8 * base['median_avg_pct']:
        out.append({'stage': 'viewers_leave_early', 'evidence': f"{row['avg_pct']:.0f}% of the length watched against a median of {base['median_avg_pct']:.0f}%"})
    if (row.get('closing_share') or 0) >= 0.35:
        out.append({'stage': 'closing_too_long', 'evidence': f"{100 * row['closing_share']:.0f}% of the spoken words are the channel closing, not content"})
    if views and views >= 300:
        if row.get('comments') == 0:out.append({'stage': 'no_comments', 'evidence': f'0 comments on {views} views'})
        if row.get('likes') is not None and row['likes'] / views < 0.01:out.append({'stage': 'few_likes', 'evidence': f"{row['likes']} likes on {views} views"})
        if row.get('subs') == 0:out.append({'stage': 'no_follow', 'evidence': f'0 subscribers from {views} views'})
    return out or [{'stage': 'ok', 'evidence': 'no measured stage below the brand median'}]


def baseline(rows):
    mature = [r for r in rows if (r.get('age_hours') or 0) >= MATURE_HOURS and r.get('views') is not None]
    med = lambda key: statistics.median(vals) if (vals := [r[key] for r in mature if r.get(key) is not None]) else None
    return {'mature_videos': len(mature), 'median_views': med('views'), 'median_engaged_pct': med('engaged_pct'), 'median_avg_pct': med('avg_pct')}


def compare(rows, key, metric='views', min_age=48):
    groups = {}
    for r in rows:
        if (r.get('age_hours') or 0) >= min_age and r.get(key) is not None and r.get(metric) is not None:
            groups.setdefault(str(r[key]), []).append(r[metric])
    return sorted(({'value': k, 'n': len(v), 'median': statistics.median(v), 'best': max(v), 'worst': min(v),
        'level': 'finding' if len(v) >= 4 else 'hint' if len(v) >= 2 else 'single'} for k, v in groups.items()), key=lambda x: -x['median'])


def rank_sum_p(a, b, rounds=20000, seed=7):
    """Two-sided permutation test of the difference in mean ranks. Small samples with outliers are exactly the case here."""
    pooled = sorted(a + b)
    def rank(x):
        low = pooled.index(x);return low + (pooled.count(x) + 1) / 2
    ranks = [rank(x) for x in a] + [rank(x) for x in b]
    observed = abs(sum(ranks[:len(a)]) / len(a) - sum(ranks[len(a):]) / len(b))
    rng = random.Random(seed);hits = 0
    for _ in range(rounds):
        rng.shuffle(ranks)
        if abs(sum(ranks[:len(a)]) / len(a) - sum(ranks[len(a):]) / len(b)) >= observed - 1e-9:hits += 1
    return (hits + 1) / (rounds + 1)


def judge(experiment, rows, today=None):
    """Read one experiment. Only mature videos with both a recorded arm and the metric count."""
    today = today or date.today()
    reader = METRIC_READERS.get(experiment.get('metric_key') or 'engaged_views', METRIC_READERS['engaged_views'])
    arms = {'control': [], 'test': []}
    for r in rows:
        arm = (r.get('experiments') or {}).get(experiment['id'])
        value = reader(r)
        if arm in arms and value is not None and (r.get('age_hours') or 0) >= MATURE_HOURS:arms[arm].append(value)
    need = experiment.get('min_samples', 8)
    a, b = arms['test'], arms['control']
    result = {'id': experiment['id'], 'brand': experiment['brand'], 'metric': experiment.get('metric_key') or 'engaged_views', 'n_test': len(a), 'n_control': len(b), 'need_per_arm': need,
        'median_test': statistics.median(a) if a else None, 'median_control': statistics.median(b) if b else None, 'decision': 'collecting'}
    expired = datetime.strptime(experiment['review_by'], '%Y-%m-%d').date() < today
    if min(len(a), len(b)) >= need:
        ctl, tst = result['median_control'], result['median_test']
        ratio = (tst + 0.5) / (ctl + 0.5)   # 0.5 keeps zero medians comparable (comments are often zero on both sides)
        result.update(ratio=round(ratio, 2), p=round(rank_sum_p(a, b), 3))
        if ratio >= ADOPT_RATIO and result['p'] <= DECISION_P:result['decision'] = 'adopt'
        elif ratio <= REJECT_RATIO and result['p'] <= DECISION_P:result['decision'] = 'reject'
        elif min(len(a), len(b)) >= 2.5 * need:result['decision'] = 'no_effect'
    if expired and result['decision'] == 'collecting':result['decision'] = 'expired'
    return result


def close_experiments(rows_by_brand, today=None, notify=None):
    """Apply decided experiments: adopted changes become standing rules; every closed one leaves the active list and the ledger gets a line."""
    import growth_brief
    results, keep = [], []
    stored = _load(growth_brief.PATH, {})
    for experiment in stored.get('experiments', []):
        result = judge(experiment, rows_by_brand.get(experiment['brand'], []), today)
        results.append(result)
        if result['decision'] in ('collecting',):
            keep.append(experiment);continue
        if result['decision'] == 'adopt':growth_brief.adopt(experiment, result)
        _ledger(experiment, result)
        if notify:notify(experiment, result)
    if len(keep) != len(stored.get('experiments', [])):
        stored['experiments'] = keep;stored['updated_at'] = time.time()
        _save(growth_brief.PATH, stored)
    return results


def _ledger(experiment, result):
    folder = BASE_DIR.parent / 'output/growth'
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / 'ledger.md').open('a') as out:
            out.write(f"{date.today().isoformat()} | {experiment['id']} ({experiment['brand']}) | {result['decision']} | {result['metric']}: "
                      f"тест {result['median_test']} (n={result['n_test']}) против контроля {result['median_control']} (n={result['n_control']}), p={result.get('p')}\n")
    except OSError:pass


def thompson_pick(brand, candidates, rows, rng=None, now=None):
    """Which ready source becomes the next video. A topic class with better measured views is sampled more often, a class with
    few videos stays in play; freshness decides between equals. Never decides for one video alone: it only reorders ready sources."""
    if not candidates:return None
    rng = rng or random
    views = [math.log1p(r['views']) for r in rows if r.get('views') is not None and (r.get('age_hours') or 0) >= 48]
    if len(views) < 6:return candidates[0]
    overall, spread = statistics.mean(views), max(statistics.pstdev(views), 0.5)
    by_class = {}
    for r in rows:
        if r.get('views') is not None and (r.get('age_hours') or 0) >= 48:by_class.setdefault(r.get('topic'), []).append(math.log1p(r['views']))
    best, best_score = candidates[0], None
    for position, source in enumerate(candidates):
        seen = by_class.get(topic_class(brand, source.get('title', '') + ' ' + source.get('text', '')[:300]), [])
        weight = len(seen) / (len(seen) + MIN_CLASS)
        mean = weight * (statistics.mean(seen) if seen else overall) + (1 - weight) * overall
        score = rng.gauss(mean, spread / math.sqrt(len(seen) + 1)) - 0.1 * position   # older sources lose a little
        if best_score is None or score > best_score:best, best_score = source, score
    return best


def pick_source(brand, candidates):
    """Pipeline hook: never lets analytics stop production."""
    try:
        latest = _load(BASE_DIR / 'output/content_analytics/latest.json', {})
        return thompson_pick(brand, candidates, rows_for(brand, latest, _load(STORE, {}), time.time())) or candidates[0]
    except Exception:return candidates[0]


def telegram_funnel(configs, now=None, get_count=None):
    """Channel subscribers over time, so 'does YouTube bring people to Telegram' is a number, not a guess."""
    now = now or time.time()
    path = DATA / 'tg_funnel.json'
    state = _load(path, {'series': {}, 'links': {}})
    for brand, cfg in configs.items():
        handle = (cfg.get('telegram') or '').lstrip('@')
        if not handle:continue
        series = state['series'].setdefault(handle, [])
        if series and now - series[-1]['t'] < 3 * 3600:continue
        try:
            count = (get_count or _member_count)(handle)
        except Exception:continue
        series.append({'t': round(now), 'members': count})
        state['series'][handle] = series[-400:]
    _refresh_links(state)
    _save(path, state)
    return state


def _secret(name, env_file='.hermes/.env'):
    """Each bot token lives in the env file of its own service, so an inherited variable of the same name is not trusted here."""
    try:
        for line in (Path.home() / env_file).read_text().splitlines():
            if line.startswith(name + '='):return line.split('=', 1)[1].strip().strip('"\'')
    except OSError:pass
    return ''


def _telegram(method, token, **data):
    import requests
    proxy = os.getenv('HTTPS_PROXY', 'http://127.0.0.1:17890')
    answer = requests.post('https://api.telegram.org/bot' + token + '/' + method, data=data, proxies={'https': proxy, 'http': proxy}, timeout=30).json()
    if not answer.get('ok'):raise RuntimeError(f"Telegram {method}: {answer.get('description')}")
    return answer['result']


def _member_count(handle):
    import requests
    proxy = os.getenv('HTTPS_PROXY', 'http://127.0.0.1:17890')
    page = requests.get('https://t.me/' + handle, proxies={'https': proxy, 'http': proxy}, timeout=30).text
    found = re.search(r'tgme_page_extra">\s*([\d\s]+?)\s*(?:subscriber|member)', page)
    if not found:raise RuntimeError('no subscriber count on the public page')
    return int(re.sub(r'\s', '', found[1]))


def _refresh_links(state):
    """Tracked invite links: Telegram counts who joined through each, so the source of a subscriber is known."""
    for handle, links in state.get('links', {}).items():
        token = _secret(links.get('token_env', ''), links.get('token_file', '.hermes/.env'))
        if not token:continue
        for name, link in links.get('items', {}).items():
            try:
                fresh = _telegram('editChatInviteLink', token, chat_id='@' + handle, invite_link=link['url'], name=name)
                link['members'] = fresh.get('member_count', 0);link['read_at'] = round(time.time())
            except Exception:pass


def funnel_summary(state, handle, youtube_views=None):
    series = state.get('series', {}).get(handle) or []
    out = {'members_now': series[-1]['members'] if series else None, 'observations': len(series)}
    if len(series) >= 2:
        out.update(gain=series[-1]['members'] - series[0]['members'], since_hours=round((series[-1]['t'] - series[0]['t']) / 3600, 1))
    links = state.get('links', {}).get(handle, {}).get('items', {})
    if links:out['joined_by_link'] = {name: link.get('members', 0) for name, link in links.items()}
    if youtube_views:out['youtube_views_since'] = {'from': SINCE, 'views': youtube_views}
    return out


def findings(brand, info):
    """Plain conclusions for the owner and the analyst, each with its numbers. Absent data produces no sentence."""
    out = []
    videos = info.get('videos', [])
    mature = [v for v in videos if (v.get('age_hours') or 0) >= 48]
    if len(mature) < 3:return [f'Роликов старше 48 ч: {len(mature)}, сравнивать рано.']
    closing = [v['closing_share'] for v in mature if v.get('closing_share') is not None]
    if closing and statistics.median(closing) >= 0.3:
        out.append(f'Концовка про канал занимает {100 * statistics.median(closing):.0f}% произносимых слов (медиана, n={len(closing)}): на содержание остаётся мало.')
    topics = [t for t in info.get('by_topic', []) if t['n'] >= 2]
    if len(topics) >= 2:
        hi, lo = topics[0], topics[-1]
        level = 'Намёк, роликов мало' if min(hi['n'], lo['n']) < 4 else 'Вывод'
        out.append(f"{level}: тема «{hi['value']}» медиана {hi['median']:.0f} (n={hi['n']}), «{lo['value']}» медиана {lo['median']:.0f} (n={lo['n']}). Выбор следующего источника учитывает это.")
    surged = sum(1 for v in mature if any(d['stage'] == 'not_distributed' for d in v.get('diagnosis', [])))
    if surged:out.append(f'{surged} из {len(mature)} роликов лента почти не показала (меньше четверти медианы).')
    comments = sum(v.get('comments') or 0 for v in mature)
    views = sum(v.get('views') or 0 for v in mature)
    if views >= 1000 and comments == 0:out.append(f'0 комментариев на {views} просмотров.')
    funnel = info.get('telegram_funnel') or {}
    if funnel.get('members_now') is not None:
        line = f"Telegram: {funnel['members_now']} подписчиков"
        if funnel.get('gain') is not None:line += f", {funnel['gain']:+d} за {funnel['since_hours']:.0f} ч"
        if funnel.get('joined_by_link'):line += ', пришли по отслеживаемой ссылке: ' + ', '.join(f'{k} {v}' for k, v in funnel['joined_by_link'].items())
        out.append(line + '.')
    return out


def run(now=None, latest=None, configs=None, notify=None):
    now = now or time.time()
    configs = configs if configs is not None else _load(DATA / 'media_brands.json', {})
    latest = latest if latest is not None else _load(BASE_DIR / 'output/content_analytics/latest.json', {})
    store = collect_api(configs, now)
    funnel = telegram_funnel(configs, now)
    report = {'updated_at': now, 'definition': {'mature': f'{MATURE_HOURS} h', 'engaged_pct': 'engaged views / views from the Analytics API; it lags 48-72 h',
        'decision': f'adopt at ratio >= {ADOPT_RATIO} and p <= {DECISION_P}, reject at ratio <= {REJECT_RATIO} and p <= {DECISION_P}'}, 'brands': {}, 'api_errors': store.get('errors')}
    rows_by_brand = {}
    for brand, cfg in configs.items():
        rows = rows_for(brand, latest, store, now)
        rows_by_brand[brand] = rows
        base = baseline(rows)
        for r in rows:r['diagnosis'] = diagnose(r, base)
        handle = (cfg.get('telegram') or '').lstrip('@')
        channel = store.get('channels', {}).get(brand, {})
        info = {'baseline': base, 'videos': sorted(rows, key=lambda r: -(r.get('views') or 0)),
            'by_topic': compare(rows, 'topic'), 'by_hour_msk': compare([{**r, 'bucket': ('ночь 0-7' if (r.get('hour_msk') or 0) < 7 else 'день 7-17' if r['hour_msk'] < 17 else 'вечер 17-24') if r.get('hour_msk') is not None else None} for r in rows], 'bucket'),
            'by_scenes': compare(rows, 'scenes'), 'by_voice': compare(rows, 'voice'),
            'traffic': channel.get('traffic'), 'countries': channel.get('countries'), 'age_gender': channel.get('age_gender'),
            'telegram_funnel': funnel_summary(funnel, handle, sum((channel.get('traffic') or {}).values()) or None)}
        info['findings'] = findings(brand, info)
        report['brands'][brand] = info
    report['experiments'] = close_experiments(rows_by_brand, notify=notify or _notify)
    _save(REPORT, report)
    return report


def _notify(experiment, result):
    try:
        import owner_notify
        words = {'adopt': 'изменение принято как правило', 'reject': 'изменение ухудшило показатель и снято', 'no_effect': 'разницы нет, снято', 'expired': 'срок вышел, данных не хватило'}
        owner_notify.send(f"🧪 <b>{owner_notify.label(experiment['brand'])}</b>: эксперимент {experiment['id']}: {words[result['decision']]}. {result['metric']}: "
            f"тест {result['median_test']} (n={result['n_test']}), контроль {result['median_control']} (n={result['n_control']}), p={result.get('p')}.",
            key='experiment:' + experiment['id'], cooldown=30 * 86400)
    except Exception:pass


def digest_lines(label=lambda brand: brand):
    report = _load(REPORT, {})
    lines = []
    for brand, info in report.get('brands', {}).items():
        for finding in info.get('findings', [])[:4]:lines.append(f'{label(brand)}: {finding}')
    for result in report.get('experiments', []):
        lines.append(f"🧪 {label(result['brand'])}, {result['id']}: тест n={result['n_test']}, контроль n={result['n_control']}, нужно по {result['need_per_arm']}")
    return lines


if __name__ == '__main__':
    result = run()
    print(json.dumps({b: i['findings'] for b, i in result['brands'].items()}, ensure_ascii=False, indent=1))
    print(json.dumps(result['experiments'], ensure_ascii=False))
