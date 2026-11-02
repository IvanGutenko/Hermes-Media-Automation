"""LVL scheduler: keeps one rendered episode ahead of the clock and publishes it per language and platform at its slot.

State is per episode in manifest.json ('published': {'telegram:ru': {...}}), so a crash never double-posts: a platform that
already holds a receipt for (episode, language) is skipped, and uncertain sends are left to the platform publishers'
own reconciliation. Slots: each language has a UTC hour; a missed slot is served late for up to LATE_SECONDS, then skipped."""
import fcntl
import json
import time
from datetime import datetime, timezone
import lvl_pipeline

BASE_DIR = lvl_pipeline.BASE_DIR
DATA = BASE_DIR / 'data'
CONFIG = DATA / 'media_brands.json'
STATE = DATA / 'lvl' / 'worker.json'
LOCK = DATA / 'lvl' / '.worker.lock'
LATE_SECONDS = 12 * 3600
PRODUCE_RETRY = 1800
HOST_BUSY_LIMIT = 4 * 3600   # a host that never calms down must not stop the series: after this long render anyway
STALL_ALERT = 24 * 3600      # no new episode for this long while the backlog is below target: tell the owner
PLATFORMS = ('telegram', 'youtube', 'instagram', 'tiktok')
DISCLAIMER = {'ru': 'Общая информация, не медицинская и не финансовая рекомендация.', 'en': 'General information, not medical or financial advice.'}
FOLLOW = {'ru': 'Следующий уровень — в канале', 'en': 'Next level in the channel'}


def now():
    return time.time()


def load_state():
    try:return json.loads(STATE.read_text())
    except (OSError, ValueError):return {}


def save_state(state):
    lvl_pipeline.save(STATE, state)


def last_slot(hour, at):
    """Most recent occurrence of `hour`:00 UTC at or before `at`."""
    d = datetime.fromtimestamp(at, timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0)
    t = d.timestamp()
    return t if t <= at else t - 86400


def episodes(status='ready'):
    out = []
    for p in sorted(lvl_pipeline.RUNS.glob('*/manifest.json')):
        try:m = json.loads(p.read_text())
        except (OSError, ValueError):continue
        if m.get('status') == status:out.append((p, m))
    return out


def unpublished(manifest, langs, platforms):
    return [(pl, lg) for lg in langs for pl in platforms if f'{pl}:{lg}' not in manifest.get('published', {})]


def enabled_platforms(cfg):
    return [p for p in PLATFORMS if cfg.get('publishing', {}).get(p, {}).get('enabled')]


def published_in_slot(key, slot):
    return any(m.get('published', {}).get(key, {}).get('at', 0) >= slot for status in ('ready', 'published') for _, m in episodes(status))


def caption(episode, lang, cfg, with_link):
    """Title, description, one-line disclaimer, hashtags. No source links: the owner ruled them out as visual noise
    (2026-11-01); the evidence stays in script.json for audit."""
    tags = ' '.join((episode.get('hashtags_' + lang) or [])[:3])
    parts = [episode['title_' + lang], episode['description_' + lang], DISCLAIMER[lang], tags]
    return '\n\n'.join(p for p in parts if p)


def payload_for(manifest, episode, lang, platform, cfg):
    video = manifest['videos'][lang]
    text = caption(episode, lang, cfg, True)
    title = episode['title_' + lang]
    if platform == 'telegram':
        short = text if len(text) <= 1000 else text[:997] + '…'
        return {'source_id': f"lvl-{manifest['run_id']}-{lang}", 'title': short, 'text': short, 'video': video, 'lang': lang}
    return {'source_id': f"lvl-{manifest['run_id']}-{lang}", 'title': title, 'text': text, 'video': video, 'lang': lang}


def publish_one(platform, lang, manifest, episode, cfg):
    import content_distribution as cd
    pcfg = cfg['publishing'][platform]
    payload = payload_for(manifest, episode, lang, platform, cfg)
    if platform == 'youtube':
        from youtube_uploader import upload_video_studio
        tags = [t.lstrip('#') for t in (episode.get('hashtags_' + lang) or [])] + ['shorts', 'LVL', 'RPG']
        description = payload['text'] + '\n\n#Shorts'
        result = upload_video_studio(manifest['videos'][lang], payload['title'], description, tags,
            {'youtube_channel_id': pcfg['channel_id'], 'youtube_channel_title': pcfg['channel_title'], 'youtube_author_url': pcfg['author_url'],
             'language': lang}, 'lvl')
        return {'status': 'published', 'url': result['url'], 'id': result['id']}
    return cd.publish('lvl', platform, payload)


def publish_due(cfg, state, log):
    """One pass over due (language, platform) pairs for the oldest ready episode; returns number of confirmed publications."""
    ready = episodes('ready')
    if not ready:return 0
    done = 0
    for lang, hour in sorted((cfg.get('post_hour_utc') or {}).items(), key=lambda kv: kv[1]):
        slot = last_slot(hour, now())
        if state.get('served', {}).get(lang, 0) >= slot or now() - slot > LATE_SECONDS:
            if now() - slot > LATE_SECONDS:state.setdefault('served', {})[lang] = slot  # missed window: wait for the next slot
            continue
        platforms = enabled_platforms(cfg)
        # One episode per slot, language and platform. Each platform takes its own oldest missing episode, so one
        # enabled later catches up on earlier episodes without holding the new one back on the others.
        slot_complete = True
        for platform in platforms:
            key = f'{platform}:{lang}'
            if published_in_slot(key, slot):continue
            entry = next(((p, m) for p, m in ready if key not in m.get('published', {})), None)
            if not entry:
                slot_complete = False
                continue
            path, manifest = entry
            script = json.loads((path.parent / 'script.json').read_text())['episode']
            try:
                result = publish_one(platform, lang, manifest, script, cfg)
            except Exception as exc:
                slot_complete = False
                log(f'{key} failed: {type(exc).__name__}: {str(exc)[:200]}')
                _alert(platform, exc)
                continue
            if result.get('status') == 'published' and result.get('url'):
                manifest.setdefault('published', {})[key] = {'url': result['url'], 'at': now()}
                lvl_pipeline.save(path, manifest)
                _announce(platform, script['title_' + lang], result['url'], lang)
                done += 1
                log(f'{key} published {result["url"]}')
            else:
                slot_complete = False
                log(f'{key} not published yet: {result.get("status")}')
        if slot_complete:state.setdefault('served', {})[lang] = slot
    for path, manifest in ready:  # a finished episode leaves the backlog on its own, so the next one gets rendered in time
        if not unpublished(manifest, list(cfg.get('post_hour_utc') or {}), enabled_platforms(cfg)):
            manifest['status'] = 'published';manifest['completed_publication_at'] = now()
            lvl_pipeline.save(path, manifest)
    return done


def _announce(platform, title, url, lang):
    try:
        import owner_notify
        owner_notify.publication('lvl', platform, 'video', title, url, lang)
    except Exception:pass


def _alert(platform, exc, cooldown=6 * 3600):
    try:
        import owner_notify
        owner_notify.failure('lvl', platform, str(exc), cooldown=cooldown)
    except Exception:pass


def watch_stall(cfg, state):
    """Rendering may be postponed for hours, never forgotten: say so when no episode has appeared for a day."""
    if now() - state.get('last_tick_at', now()) > 3600:state['watch_since'] = now()  # the series was off or the timer stopped: count from now
    state['last_tick_at'] = now()
    since = max(state.get('last_produced_at', 0), state.setdefault('watch_since', now()))
    if len(episodes('ready')) >= cfg.get('backlog_target', 2) or now() - since < STALL_ALERT:return False
    why = state.get('last_produce_error') or ('the server stayed too busy since ' + time.strftime('%H:%M UTC', time.gmtime(state['host_busy_since'])) if state.get('host_busy_since') else 'no reason recorded')
    _alert('production', RuntimeError(f'no new episode for {int((now() - since) / 3600)} h; {why}'[:300]), cooldown=12 * 3600)
    return True


def need_production(cfg, state):
    backlog = len(episodes('ready'))
    if backlog >= cfg.get('backlog_target', 2):return False
    if now() - state.get('produce_failed_at', 0) < PRODUCE_RETRY:return False
    return True


def tick(log=print):
    cfg = json.loads(CONFIG.read_text()).get('lvl')
    if not cfg or not cfg.get('lvl_enabled'):
        log('lvl disabled');return {'status': 'disabled'}
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a') as lock:
        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:return {'status': 'busy'}
        state = load_state()
        published = publish_due(cfg, state, log)
        save_state(state)
        produced = None
        if need_production(cfg, state):
            force = bool(state.get('host_busy_since')) and now() - state['host_busy_since'] > HOST_BUSY_LIMIT
            try:
                produced = lvl_pipeline.produce(log=log, force=force)
                state['produce_failures'] = 0
                state['last_produced_at'] = now()
                state.pop('host_busy_since', None)
                state.pop('last_produce_error', None)
            except lvl_pipeline.HostBusy as exc:  # the server is short of memory: wait, no failure to report
                state['produce_failed_at'] = now()
                state.setdefault('host_busy_since', now())
                log(str(exc))
            except Exception as exc:
                state['produce_failed_at'] = now()
                state['produce_failures'] = state.get('produce_failures', 0) + 1
                state['last_produce_error'] = f'{type(exc).__name__}: {str(exc)[:300]}'
                log('production failed: ' + state['last_produce_error'])
                if state['produce_failures'] in (3, 10):_alert('production', RuntimeError(state['last_produce_error']))
            save_state(state)
        watch_stall(cfg, state)
        save_state(state)
        return {'status': 'ok', 'published': published, 'produced': produced and produced['run_id']}


if __name__ == '__main__':
    print(json.dumps(tick(), ensure_ascii=False))
