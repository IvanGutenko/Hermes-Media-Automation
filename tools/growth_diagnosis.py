"""Why did each video do what it did? Measured diagnosis for the growth analyst and the owner digest.

Pure arithmetic over data we already collect (counters, series, Studio overview snapshots). Every statement carries the sample
size behind it; a comparison with fewer than MIN_SIDE videos on a side is labelled a hint, never a finding. Rules for the
per-video flags come from config/growth-playbook.md (platform behaviour verified there) and are thresholds, not claims about cause."""
import json
import os
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(os.getenv('VIDEO_FACTORY_DIR', Path(__file__).resolve().parent.parent))
DATA = BASE_DIR / 'data'
OUT = DATA / 'growth_diagnosis.json'
MATURE_HOURS = 48     # Shorts counters are mostly settled by then, so the baseline uses videos at least this old
MIN_SIDE = 4          # videos needed on each side of a feature comparison to call it a finding
STAY_WEAK, STAY_GOOD = 50.0, 65.0   # "stayed to watch" thresholds from the playbook (below weak: fix the first seconds)
STOP = {'как', 'что', 'для', 'это', 'при', 'или', 'так', 'его', 'они', 'про', 'with', 'from', 'that', 'this', 'what', 'your'}


def words(title):
    return [w[:6] for w in re.findall(r'[a-zа-яё0-9]{4,}', (title or '').lower()) if w not in STOP and not w.isdigit()]


def features(item):
    """Observable properties of a video, each True/False or a bucket, used only to compare groups."""
    title = item.get('title') or ''
    duration = (item.get('metrics') or {}).get('duration_s') or item.get('duration')
    hour = datetime.fromtimestamp(item['published_at'], timezone.utc).hour if item.get('published_at') else None
    return {
        'title_has_number': bool(re.search(r'\d', title)),
        'title_is_question': '?' in title,
        'title_has_colon': ':' in title,
        'title_long_over_55': len(title) > 55,
        'title_starts_how_why': bool(re.match(r'\s*(как|почему|зачем|что|how|why|what)\b', title.lower())),
        'duration_over_20s': bool(duration and duration > 20),
        'posted_evening_utc_15_21': bool(hour is not None and 15 <= hour <= 21),
        'posted_night_utc_0_6': bool(hour is not None and 0 <= hour <= 6),
    }


def lift(rows, name, key='score'):
    with_f = [r[key] for r in rows if r['features'].get(name)]
    without = [r[key] for r in rows if not r['features'].get(name)]
    if not with_f or not without:return None
    a, b = statistics.median(with_f), statistics.median(without)
    ratio = round(a / b, 2) if b else None
    status = 'finding' if min(len(with_f), len(without)) >= MIN_SIDE and ratio and (ratio >= 1.5 or ratio <= 0.67) else 'hint' if min(len(with_f), len(without)) >= 2 else 'too_few'
    return {'feature': name, 'n_with': len(with_f), 'n_without': len(without), 'median_with': round(a, 2), 'median_without': round(b, 2), 'ratio': ratio, 'status': status}


def token_lifts(rows, limit=6):
    """Title words that recur in strong or weak videos. Hints only: needs two or more videos per word."""
    strong = {r['url'] for r in rows if r['score'] >= 1.5}
    weak = {r['url'] for r in rows if r['score'] <= 0.5}
    seen = {}
    for r in rows:
        for w in set(words(r['title'])):seen.setdefault(w, set()).add(r['url'])
    up = sorted(((w, len(u & strong), len(u)) for w, u in seen.items() if len(u & strong) >= 2 and not (u & weak)), key=lambda x: -x[1])[:limit]
    down = sorted(((w, len(u & weak), len(u)) for w, u in seen.items() if len(u & weak) >= 2 and not (u & strong)), key=lambda x: -x[1])[:limit]
    fmt = lambda t: [{'stem': w, 'in_group': a, 'videos_with_word': b} for w, a, b in t]
    return {'in_strong_only': fmt(up), 'in_weak_only': fmt(down)}


def video_flags(row, overview):
    """Rule-based observations for one video; each names the number behind it."""
    flags = []
    views, likes, comments = row['views'], row.get('likes'), row.get('comments')
    stay = overview.get('stayed_to_watch_pct')
    if stay is not None:
        if stay < STAY_WEAK:flags.append(f'weak_hook: only {stay:.0f}% stay past the first seconds (playbook target >= {STAY_GOOD:.0f}%)')
        elif stay >= STAY_GOOD:flags.append(f'strong_hook: {stay:.0f}% stay to watch')
    pct = overview.get('average_percentage_viewed')
    if pct is not None and pct < 60 and (stay or 100) >= STAY_WEAK:flags.append(f'mid_video_drop: viewers leave after the hook, average {pct:.0f}% watched')
    if views >= 300 and likes is not None and likes / views < 0.01:flags.append(f'low_likes: {likes}/{views} ({100 * likes / views:.1f}%)')
    if views >= 300 and comments == 0:flags.append('no_comments: add one concrete question viewers can answer in a word')
    if views >= 300 and overview.get('subscribers_gained') == 0:flags.append('no_follow: views without subscribers, the finish needs a series promise')
    sources = overview.get('traffic_sources') or {}
    if sources:
        top = max(sources.items(), key=lambda kv: kv[1])
        if top[1] >= 60:flags.append(f'traffic_concentrated: {top[1]:.0f}% from {top[0]}')
    return flags


def build_brand(items, overview, now):
    yt = [i for i in items if i['platform'] == 'youtube' and (i.get('metrics') or {}).get('views') is not None and i.get('published_at')]
    rows = []
    for i in yt:
        age = (now - i['published_at']) / 3600
        rows.append({'url': i['url'], 'title': i.get('title') or '', 'age_hours': round(age, 1), 'views': i['metrics']['views'], 'likes': i['metrics'].get('likes'),
            'comments': i['metrics'].get('comments'), 'features': features(i), 'voice': i.get('voice')})
    mature = [r for r in rows if r['age_hours'] >= MATURE_HOURS]
    if len(mature) < 3:
        return {'status': 'insufficient_baseline', 'mature_videos': len(mature), 'videos_total': len(rows),
            'note': f'need at least 3 videos older than {MATURE_HOURS} h for a baseline; collecting'}
    views = sorted(r['views'] for r in mature)
    base = statistics.median(views)
    p75 = views[int(0.75 * (len(views) - 1))]
    for r in rows:
        r['score'] = round(r['views'] / base, 2) if base else 0.0
    scored = [r for r in rows if r['age_hours'] >= 24]
    for r in rows:
        r['flags'] = video_flags(r, overview.get(r['url']) or {})
    surges = [r for r in rows if r['age_hours'] < 24 and base and r['views'] >= 2 * base]
    ranked = sorted(scored, key=lambda r: -r['score'])
    feats = [x for x in (lift(scored, name) for name in features({'published_at': now}) if scored) if x]
    voices = {}
    for r in scored:
        if r.get('voice'):voices.setdefault(r['voice'], []).append(r['score'])
    out = {'status': 'ok', 'baseline': {'mature_videos': len(mature), 'median_views': base, 'p75_views': p75, 'min': views[0], 'max': views[-1]},
        'videos_total': len(rows), 'videos_scored': len(scored),
        'top': [slim(r) for r in ranked[:3]], 'bottom': [slim(r) for r in ranked[-3:][::-1] if r['score'] < 0.7],
        'early_surges': [slim(r) for r in sorted(surges, key=lambda r: -r['views'])[:3]],
        'features': sorted(feats, key=lambda x: (x['status'] != 'finding', -abs((x['ratio'] or 1) - 1))),
        'title_words': token_lifts(scored),
        'voices': [{'voice': v, 'n': len(s), 'median_score': round(statistics.median(s), 2)} for v, s in voices.items() if len(s) >= 2],
        'comparisons_made': len(feats), 'caution': 'Many comparisons on few videos: treat hints as questions to test, findings as the first thing to test.'}
    out['hypotheses'] = hypotheses(out)
    return out


def slim(r):
    return {'title': r['title'][:80], 'url': r['url'], 'age_hours': r['age_hours'], 'views': r['views'], 'score': r.get('score'), 'likes': r.get('likes'),
        'comments': r.get('comments'), 'flags': r.get('flags', [])}


def hypotheses(info):
    """Plain-language questions worth testing next, each tied to the numbers that raise it."""
    out = []
    for f in info['features']:
        if f['status'] == 'finding':
            direction = 'higher' if f['ratio'] >= 1 else 'lower'
            out.append(f"{f['feature']}: median {direction} by x{f['ratio']} ({f['n_with']} vs {f['n_without']} videos). Test it deliberately in the next batch.")
    for w in info['title_words']['in_strong_only']:
        out.append(f"title stem '{w['stem']}' appears in {w['in_group']} strong videos and in no weak one ({w['videos_with_word']} uses). Topic or wording worth repeating.")
    for w in info['title_words']['in_weak_only']:
        out.append(f"title stem '{w['stem']}' appears in {w['in_group']} weak videos and in no strong one. Consider dropping the angle.")
    for s in info['early_surges']:
        out.append(f"early surge: '{s['title']}' has {s['views']} views in {s['age_hours']:.0f} h (baseline median {info['baseline']['median_views']}). Make a follow-up on the same angle while it runs.")
    for r in info['top'][:1] + info['bottom'][:1]:
        for flag in r['flags'][:2]:out.append(f"'{r['title'][:50]}': {flag}")
    return out[:10]


def channel_funnel(series_rows, channel_id):
    """Subscribers gained per 1,000 views between the first and last observation of a channel."""
    pts = [r for r in series_rows if r.get('k') == 'channel:' + channel_id and r.get('subscribers') is not None and r.get('views') is not None]
    if len(pts) < 2:return {'status': 'need_more_observations', 'observations': len(pts)}
    a, b = pts[0], pts[-1]
    dv, ds = b['views'] - a['views'], b['subscribers'] - a['subscribers']
    return {'status': 'ok', 'since_hours': round((b['t'] - a['t']) / 3600, 1), 'views_gained': dv, 'subscribers_gained': ds,
        'subscribers_per_1000_views': round(1000 * ds / dv, 2) if dv > 0 else None, 'subscribers_now': b['subscribers']}


def read_series(path):
    rows = []
    try:
        for line in Path(path).read_text().splitlines():
            try:rows.append(json.loads(line))
            except ValueError:pass
    except OSError:pass
    return rows


def run(now=None, latest=None, overview=None, series=None, configs=None):
    now = now or time.time()
    latest = latest if latest is not None else json.loads((BASE_DIR / 'output/content_analytics/latest.json').read_text())
    overview = overview if overview is not None else json.loads((DATA / 'youtube_overview.json').read_text() or '{}')
    series = series if series is not None else read_series(DATA / 'metrics_series.jsonl')
    configs = configs if configs is not None else json.loads((DATA / 'media_brands.json').read_text())
    latest_overview = {u: max(v['snapshots'], key=lambda s: s['observed_at']) for u, v in overview.items() if v.get('snapshots')}
    result = {'updated_at': now, 'brands': {}, 'definition': {'score': 'views divided by the brand median of videos at least 48 h old', 'early_surge': 'under 24 h old with at least 2x the baseline'}}
    for brand, cfg in configs.items():
        items = [i for i in latest['items'] if i['brand'] == brand]
        if not items and not cfg.get('youtube_channel_id'):continue
        info = build_brand(items, latest_overview, now)
        if cfg.get('youtube_channel_id'):info['channel'] = channel_funnel(series, cfg['youtube_channel_id'])
        result['brands'][brand] = info
    tmp = OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(OUT)
    return result


def digest_lines(result=None, label=lambda brand: brand):
    """Few Russian lines for the owner digest: facts only."""
    result = result or json.loads(OUT.read_text())
    lines = []
    for brand, info in result['brands'].items():
        if info.get('status') != 'ok':
            lines.append(f'{label(brand)}: база для сравнения ещё собирается ({info.get("mature_videos", 0)} роликов старше {MATURE_HOURS} ч)')
            continue
        b = info['baseline']
        line = f"{label(brand)}: медиана {b['median_views']:.0f} просмотров (n={b['mature_videos']})"
        if info['top']:line += f"; лидер «{info['top'][0]['title'][:40]}» x{info['top'][0]['score']}"
        if info['early_surges']:line += f"; сейчас растёт «{info['early_surges'][0]['title'][:40]}» ({info['early_surges'][0]['views']})"
        ch = info.get('channel') or {}
        if ch.get('subscribers_per_1000_views') is not None:line += f"; подписчиков на 1000 просмотров: {ch['subscribers_per_1000_views']}"
        lines.append(line)
    return lines


if __name__ == '__main__':
    r = run()
    print(json.dumps({'brands': {b: v.get('status') for b, v in r['brands'].items()}, 'lines': digest_lines(r)}, ensure_ascii=False))
