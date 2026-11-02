"""YouTube Analytics API, read-only: per-video retention, subscribers and traffic sources without opening Studio.

Studio pages were the heaviest thing the hourly pass loaded into the shared 4.9 GB browser. The API returns the
same figures except one: Studio's Shorts "Stayed to watch %" has no API metric (checked against the official metrics
page, updated 2026-09-14), so that number alone still comes from Studio, read rarely. A value the API does not
return stays absent; it is never turned into zero.

Consent is per channel identity: the owner approves once for each brand channel (account chooser), the redirected
`http://localhost/?code=...` URL is passed to `exchange`, and the refresh token is stored 0600 in
data/yt_analytics/<channel_id>.json. Tokens, codes and the client secret are never printed or put into errors."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from script_generator import BASE_DIR

DATA = BASE_DIR / 'data'
TOKENS = DATA / 'yt_analytics'
SECRET = BASE_DIR / 'client_secret.json'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
REPORTS = 'https://youtubeanalytics.googleapis.com/v2/reports'
CHANNELS = 'https://www.googleapis.com/youtube/v3/channels'
REDIRECT = 'http://localhost'
SCOPES = ('https://www.googleapis.com/auth/youtube.readonly', 'https://www.googleapis.com/auth/yt-analytics.readonly')
ANALYTICS_SCOPE = SCOPES[1]
# insightTrafficSourceType codes -> the labels Studio shows (growth_diagnosis reads these as plain text).
SOURCE_LABELS = {'SHORTS': 'Shorts feed', 'YT_SEARCH': 'YouTube search', 'RELATED_VIDEO': 'Suggested videos', 'SUBSCRIBER': 'Browse features',
                 'YT_CHANNEL': 'Channel pages', 'EXT_URL': 'External', 'NOTIFICATION': 'Notifications', 'PLAYLIST': 'Playlists',
                 'YT_OTHER_PAGE': 'Other YouTube features', 'NO_LINK_OTHER': 'Direct or unknown', 'NO_LINK_EMBEDDED': 'Embedded players',
                 'ADVERTISING': 'Advertising', 'HASHTAGS': 'Hashtag pages', 'CAMPAIGN_CARD': 'Cards', 'END_SCREEN': 'End screens',
                 'SOUND_PAGE': 'Sound pages', 'LIVE_REDIRECT': 'Live redirects', 'PRODUCT_PAGE': 'Product pages', 'VIDEO_REMIXES': 'Remixes'}
_cache = {}


class AnalyticsError(RuntimeError):
    pass


def _request(request):
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:body = json.loads(exc.read().decode())
        except (ValueError, OSError):body = {}
        error = body.get('error')
        detail = error.get('message') if isinstance(error, dict) else (error or '')
        detail = f"{detail} {body.get('error_description') or ''}".strip()
        raise AnalyticsError(f'HTTP {exc.code}: {detail[:200]}') from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise AnalyticsError(type(exc).__name__) from None


def _post(fields):
    return _request(urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(fields).encode()))


def _get(url, params, token):
    return _request(urllib.request.Request(url + '?' + urllib.parse.urlencode(params), headers={'Authorization': 'Bearer ' + token}))


def _client():
    value = json.loads(SECRET.read_text())
    value = value.get('installed') or value.get('web') or value
    return value['client_id'], value['client_secret']


def consent_url():
    client_id, _ = _client()
    query = urllib.parse.urlencode({'client_id': client_id, 'redirect_uri': REDIRECT, 'response_type': 'code', 'access_type': 'offline',
                                    'prompt': 'consent select_account', 'include_granted_scopes': 'false', 'scope': ' '.join(SCOPES)})
    return 'https://accounts.google.com/o/oauth2/auth?' + query


def parse_redirect(url):
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url.strip()).query)
    if query.get('error'):raise AnalyticsError('consent refused: ' + query['error'][0][:80])
    if not query.get('code'):raise AnalyticsError('the pasted address has no code= parameter')
    return query['code'][0]


def exchange(redirect_url):
    """Turns the redirected address into a stored refresh token; returns only {channel_id, channel_title, scopes}."""
    client_id, client_secret = _client()
    granted = _post({'code': parse_redirect(redirect_url), 'client_id': client_id, 'client_secret': client_secret,
                     'redirect_uri': REDIRECT, 'grant_type': 'authorization_code'})
    scopes = granted.get('scope', '').split()
    if ANALYTICS_SCOPE not in scopes:raise AnalyticsError('yt-analytics.readonly was not granted')
    if not granted.get('refresh_token'):raise AnalyticsError('Google returned no refresh token')
    answer = _get(CHANNELS, {'part': 'id,snippet', 'mine': 'true'}, granted['access_token'])
    items = answer.get('items') or []
    if len(items) != 1:raise AnalyticsError(f'expected one channel for this consent, got {len(items)}')
    channel = {'channel_id': items[0]['id'], 'channel_title': items[0]['snippet']['title']}
    TOKENS.mkdir(parents=True, exist_ok=True)
    path = TOKENS / (channel['channel_id'] + '.json')
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump({**channel, 'refresh_token': granted['refresh_token'], 'scopes': scopes, 'created_at': time.time()}, handle)
    tmp.replace(path)
    _cache.pop(channel['channel_id'], None)
    return {**channel, 'scopes': scopes}


def channels():
    return sorted(p.stem for p in TOKENS.glob('*.json')) if TOKENS.is_dir() else []


def access_token(channel_id, now=None):
    now = now or time.time()
    cached = _cache.get(channel_id)
    if cached and cached[1] > now + 60:return cached[0]
    stored = json.loads((TOKENS / (channel_id + '.json')).read_text())
    client_id, client_secret = _client()
    answer = _post({'refresh_token': stored['refresh_token'], 'client_id': client_id, 'client_secret': client_secret, 'grant_type': 'refresh_token'})
    _cache[channel_id] = (answer['access_token'], now + int(answer.get('expires_in', 3000)))
    return _cache[channel_id][0]


def _rows(answer):
    names = [c['name'] for c in answer.get('columnHeaders', [])]
    return [dict(zip(names, row)) for row in answer.get('rows') or []]


def video_overview(channel_id, video_id, published_at, now=None):
    """Same keys as studio_overview.parse where the API has them, plus engaged_views_pct; {} while the API has no row yet."""
    now = now or time.time()
    token = access_token(channel_id, now)
    start = datetime.fromtimestamp(published_at, timezone.utc).strftime('%Y-%m-%d')
    end = datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d')
    base = {'ids': 'channel==MINE', 'startDate': start, 'endDate': end, 'filters': 'video==' + video_id}
    rows = _rows(_get(REPORTS, {**base, 'metrics': 'views,engagedViews,averageViewDuration,averageViewPercentage,subscribersGained,likes,comments,shares'}, token))
    if not rows or rows[0].get('views') is None:return {}
    row = rows[0]
    result = {'views': int(row['views']), 'overview_observed_at': now, 'retention_source': 'YouTube Analytics API'}
    if row.get('averageViewDuration') is not None:result['average_view_duration_s'] = round(float(row['averageViewDuration']))
    if row.get('averageViewPercentage') is not None:result['average_percentage_viewed'] = round(float(row['averageViewPercentage']), 1)
    if row.get('subscribersGained') is not None:result['subscribers_gained'] = int(row['subscribersGained'])
    if row.get('engagedViews') is not None and result['views']:result['engaged_views_pct'] = round(100 * float(row['engagedViews']) / result['views'], 1)
    try:
        sources = _rows(_get(REPORTS, {**base, 'dimensions': 'insightTrafficSourceType', 'metrics': 'views', 'sort': '-views'}, token))
    except AnalyticsError:
        sources = []   # the retention figures above are still valid
    total = sum(float(s['views']) for s in sources)
    if total:
        shares = {}
        for source in sources:
            label = SOURCE_LABELS.get(source['insightTrafficSourceType'], source['insightTrafficSourceType'])
            shares[label] = round(shares.get(label, 0) + 100 * float(source['views']) / total, 1)
        result['traffic_sources'] = shares
    return result


if __name__ == '__main__':
    import sys
    if sys.argv[1:2] == ['consent-url']:print(consent_url())
    elif sys.argv[1:2] == ['exchange']:print(json.dumps(exchange(sys.stdin.read()), ensure_ascii=False))   # the address comes on stdin, not in argv
    elif sys.argv[1:2] == ['channels']:print(json.dumps(channels()))
    else:print('usage: yt_analytics.py consent-url | exchange (redirected address on stdin) | channels')
