"""Public YouTube counters straight from the Data API, without the shared browser lease.

The browser collector failed on 101 of 120 runs (2026-10-31/11-01) because Flow generation holds
the one browser lease for long stretches. Views, likes and comments of any public video, and the
public subscriber count of a channel, need no browser: one authorised read-only request each."""
import json
import re
import time
from pathlib import Path

API = 'https://www.googleapis.com/youtube/v3/'
VIDEO_ID = re.compile(r'(?:youtu\.be/|[?&]v=|/shorts/)([A-Za-z0-9_-]{11})')


def video_id(url):
    match = VIDEO_ID.search(url or '')
    return match[1] if match else None


def _count(value):
    try:return int(value)
    except (TypeError, ValueError):return None


def parse_duration(value):
    """ISO-8601 'PT1M16S' to seconds; None for anything else."""
    match = re.fullmatch(r'P(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)', value or '')
    if not match or not any(match.groups()):return None
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def videos(session, ids, now=None):
    """{'https://youtu.be/<id>': counters} for up to any number of ids; a hidden counter stays None, never 0."""
    now = now or time.time()
    result, unique = {}, list(dict.fromkeys(i for i in ids if i))
    for start in range(0, len(unique), 50):
        batch = unique[start:start + 50]
        response = session.get(API + 'videos', params={'part': 'statistics,contentDetails,snippet,status', 'id': ','.join(batch)}, timeout=40)
        if not response.ok:raise RuntimeError(f'YouTube Data API HTTP {response.status_code}')
        for item in response.json().get('items', []):
            stats = item.get('statistics', {})
            result['https://youtu.be/' + item['id']] = {
                'views': _count(stats.get('viewCount')), 'likes': _count(stats.get('likeCount')),
                'comments': _count(stats.get('commentCount')), 'duration_s': parse_duration(item.get('contentDetails', {}).get('duration')),
                'title': item.get('snippet', {}).get('title'), 'channel_id': item.get('snippet', {}).get('channelId'),
                'published_at_iso': item.get('snippet', {}).get('publishedAt'),
                'privacy': item.get('status', {}).get('privacyStatus'),
                'observed_at': now, 'source': 'YouTube Data API', 'window': 'since publication', 'platform': 'youtube'}
    return result


def channels(session, ids, now=None):
    """{channel_id: {'subscribers', 'views', 'videos'}}; subscribers is None when the channel hides the count."""
    now = now or time.time()
    result = {}
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:return result
    response = session.get(API + 'channels', params={'part': 'statistics', 'id': ','.join(ids)}, timeout=40)
    if not response.ok:raise RuntimeError(f'YouTube Data API HTTP {response.status_code}')
    for item in response.json().get('items', []):
        stats = item.get('statistics', {})
        result[item['id']] = {'subscribers': None if stats.get('hiddenSubscriberCount') else _count(stats.get('subscriberCount')),
                              'views': _count(stats.get('viewCount')), 'videos': _count(stats.get('videoCount')), 'observed_at': now}
    return result


def append_series(path, rows, now=None, min_gap=3300, keep_days=30):
    """Append {'k': key, 't': epoch, **values} rows to a JSON-lines time series.

    A row is written only when a value changed or the previous row for that key is older than
    min_gap seconds, so a quiet video costs one line an hour. Lines older than keep_days are dropped
    when the file is rewritten (about once a day)."""
    now = now or time.time()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    last = {}
    if path.exists():
        for line in path.read_text().splitlines()[-4000:]:
            try:row = json.loads(line)
            except ValueError:continue
            last[row.get('k')] = row
    fresh = []
    for key, values in rows.items():
        values = {k: v for k, v in values.items() if v is not None}
        if not values:continue
        old = last.get(key)
        if old and now - old.get('t', 0) < min_gap and all(old.get(k) == v for k, v in values.items()):continue
        if old and now - old.get('t', 0) < 600:continue
        fresh.append({'k': key, 't': round(now), **values})
    if fresh:
        with path.open('a') as stream:
            for row in fresh:stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    if path.exists() and now - path.stat().st_ctime > 86400 and path.stat().st_size > 2_000_000:
        keep = [line for line in path.read_text().splitlines() if _recent(line, now - keep_days * 86400)]
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text('\n'.join(keep) + '\n')
        tmp.replace(path)
    return len(fresh)


def _recent(line, cutoff):
    try:return json.loads(line).get('t', 0) >= cutoff
    except ValueError:return False


def read_series(path, key=None, since=0):
    path = Path(path)
    if not path.exists():return []
    rows = []
    for line in path.read_text().splitlines():
        try:row = json.loads(line)
        except ValueError:continue
        if row.get('t', 0) >= since and (key is None or row.get('k') == key):rows.append(row)
    return rows
