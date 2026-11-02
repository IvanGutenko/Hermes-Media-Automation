"""Parse the text of a YouTube Studio 'Video analytics / Overview' page (English UI, ?hl=en).

Observed 2026-11-01 for a Short: 'Stayed to watch 54.0%' (the share that did not swipe away),
'Average view duration 0:18', 'Subscribers' and a 'Top traffic sources' list. A value the page does
not show stays None; it is never turned into zero."""
import re

NUMBER = re.compile(r'^[+-]?[\d.,\s]+[KMкКмМ]?$')


def _count(text):
    from metrics_numbers import parse_count
    return parse_count(text)


def _after(lines, label, offset=1):
    for index, line in enumerate(lines):
        if line.strip().lower() == label.lower() and index + offset < len(lines):
            return lines[index + offset].strip()
    return None


def _percent(text):
    match = re.fullmatch(r'([\d.,]+)\s*%', (text or '').strip())
    return float(match[1].replace(',', '.')) if match else None


def _seconds(text):
    match = re.fullmatch(r'(?:(\d+):)?(\d{1,2}):(\d{2})', (text or '').strip())
    if not match:return None
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def parse(body):
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    result = {}
    views = None
    for index, line in enumerate(lines):
        if line == 'Views' and index + 2 < len(lines) and lines[index + 1] == 'Views' and NUMBER.match(lines[index + 2]):
            views = _count(lines[index + 2]);break
    if views is not None:result['views'] = views
    subs = _after(lines, 'Subscribers')
    if subs and NUMBER.match(subs):
        magnitude = _count(subs.lstrip('+-'))
        if magnitude is not None:result['subscribers_gained'] = -magnitude if subs.startswith('-') else magnitude
    stayed = _percent(_after(lines, 'Stayed to watch'))
    if stayed is None:
        # Older layout: 'Viewed vs swiped away' with a 'Viewed' share.
        for index, line in enumerate(lines):
            if line.lower() == 'viewed' and index + 1 < len(lines) and _percent(lines[index + 1]) is not None:
                stayed = _percent(lines[index + 1]);break
    if stayed is not None:result['stayed_to_watch_pct'] = stayed
    duration = _seconds(_after(lines, 'Average view duration'))
    if duration is not None:result['average_view_duration_s'] = duration
    pct = _percent(_after(lines, 'Average percentage viewed'))
    if pct is not None:result['average_percentage_viewed'] = pct
    sources = {}
    if 'Top traffic sources' in lines:
        for index in range(lines.index('Top traffic sources') + 1, len(lines) - 1):
            if lines[index].lower().startswith('see more'):break
            share = _percent(lines[index + 1])
            if share is not None and not _percent(lines[index]) and lines[index] not in ('Views', 'Watch time'):
                sources[lines[index]] = share
    if sources:result['traffic_sources'] = sources
    return result


def complete(parsed):
    """True once the page showed the retention numbers, so a half-rendered page is not stored."""
    return 'stayed_to_watch_pct' in parsed or 'average_view_duration_s' in parsed
