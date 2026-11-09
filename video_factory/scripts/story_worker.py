"""StoryWobble schedule: publish a reviewed episode once per interval, then make the next.

Owner instruction 2026-11-08: build the pipeline and publish automatically to YouTube and
Instagram, about one video a day so Flow stays available for the other channels; Telegram
stays off. Every publication requires the reviewed master (story_pipeline.require_artifact)
and a confirmed URL; an uncertain upload is never repeated blindly.
"""
import fcntl
import json
import os
import time
from pathlib import Path

import story_pipeline as sp

BASE_DIR = sp.BASE_DIR
CONFIG = BASE_DIR / 'data' / 'media_brands.json'
STATE = sp.DATA / 'worker.json'
LOCK = sp.DATA / '.worker.lock'
PLATFORMS = ('youtube', 'instagram')
DEFAULT_INTERVAL = 86400
RETRY = 1800
LOGIN_RETRY = 6 * 3600


def now():
    return time.time()


def enabled(cfg):
    return [p for p in PLATFORMS if cfg.get('publishing', {}).get(p, {}).get('enabled')]


def episodes(status=('ready',)):
    out = []
    for path in sorted(sp.RUNS.glob('*/manifest.json')):
        m = sp.load(path)
        if m.get('status') in status:out.append((path, m))
    return out


def last_published(platform):
    at = 0
    for _, m in episodes(('ready', 'published')):
        at = max(at, (m.get('published', {}).get(platform) or {}).get('at', 0))
    return at


def caption(script):
    tags = ' '.join((script.get('hashtags_ru') or [])[:3])
    return '\n\n'.join(p for p in (script['title_ru'], script['description_ru'], tags) if p)


def publish_one(platform, manifest, script, cfg):
    video = sp.require_artifact(manifest)
    pcfg = cfg['publishing'][platform]
    text = caption(script)
    if platform == 'youtube':
        from youtube_uploader import upload_video_studio
        tags = [t.lstrip('#') for t in (script.get('hashtags_ru') or [])] + ['shorts', 'StoryWobble', 'анимация', 'история']
        result = upload_video_studio(str(video), script['title_ru'], text + '\n\n#Shorts', tags,
            {'youtube_channel_id': pcfg['channel_id'], 'youtube_channel_title': pcfg['channel_title'],
             'youtube_author_url': pcfg['author_url'], 'language': 'ru'}, sp.BRAND)
        return {'status': 'published', 'url': result['url'], 'id': result.get('id')}
    import content_distribution as cd
    return cd.publish(sp.BRAND, platform, {'source_id': 'story-' + manifest['run_id'], 'title': script['title_ru'],
                                           'text': text, 'video': str(video), 'lang': 'ru'})


def publish_due(cfg, state, log):
    done = 0
    for platform in enabled(cfg):
        interval = cfg['publishing'][platform].get('video_interval_seconds', DEFAULT_INTERVAL)
        if now() - last_published(platform) < interval:continue
        if state.get('retry', {}).get(platform, 0) > now():continue
        entry = next(((p, m) for p, m in episodes() if platform not in m.get('published', {})), None)
        if not entry:continue
        path, manifest = entry
        script = sp.load(path.parent / 'script.json')
        try:
            result = publish_one(platform, manifest, script, cfg)
        except Exception as exc:
            # A missing login heals only when the owner signs in; do not reopen the shared browser every half hour.
            state.setdefault('retry', {})[platform] = now() + (LOGIN_RETRY if 'NeedsLogin' in f'{type(exc).__name__} {exc}' else RETRY)
            log(f'storywobble {platform} failed: {type(exc).__name__}: {str(exc)[:300]}')
            _notify('failure', platform, str(exc))
            continue
        if result.get('status') == 'published' and result.get('url'):
            manifest = sp.load(path)
            manifest.setdefault('published', {})[platform] = {'url': result['url'], 'at': now(), 'sha256': manifest.get('sha256')}
            if all(p in manifest['published'] for p in enabled(cfg)):
                manifest.update(status='published', completed_publication_at=now())
            sp.save(path, manifest);done += 1
            state.get('retry', {}).pop(platform, None)
            log(f'storywobble {platform} published {result["url"]}')
            _notify('publication', platform, script['title_ru'], result['url'])
        else:
            state.setdefault('retry', {})[platform] = now() + RETRY
            log(f'storywobble {platform} not published yet: {result.get("status")}')
    return done


def _notify(kind, platform, *args):
    try:
        import owner_notify
        if kind == 'publication':owner_notify.publication(sp.BRAND, platform, 'video', args[0], args[1], 'ru')
        else:owner_notify.failure(sp.BRAND, platform, args[0], cooldown=6 * 3600)
    except Exception:pass


def need_production(cfg, state):
    if state.get('produce_retry_after', 0) > now():return False
    # A platform resting for hours (lost login) must not stop the others' daily episode.
    platforms = [p for p in enabled(cfg) if state.get('retry', {}).get(p, 0) - now() < 3600] or enabled(cfg)
    waiting = [m for _, m in episodes() if any(p not in m.get('published', {}) for p in platforms)]
    return len(waiting) < cfg.get('backlog_target', 1)


def tick(log=print):
    cfg = json.loads(CONFIG.read_text()).get(sp.BRAND)
    if not cfg or not cfg.get('story_enabled'):
        return {'status': 'disabled'}
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a') as lock:
        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:return {'status': 'busy'}
        state = sp.load(STATE)
        published = publish_due(cfg, state, log)
        sp.save(STATE, state)
        produced = None
        if need_production(cfg, state):
            try:
                produced = sp.produce(log=log, force=now() - state.get('busy_since', now()) > 4 * 3600)['run_id']
                state.update(last_produced_at=now(), failures=0)
                for key in ('busy_since', 'last_error', 'produce_retry_after'):state.pop(key, None)
                published += publish_due(cfg, state, log)
            except sp.MediaPending as exc:
                state['produce_retry_after'] = exc.retry_after
                state['last_error'] = str(exc)[-300:]
                if 'host busy' in str(exc):state.setdefault('busy_since', now())
                log('StoryWobble waits: ' + state['last_error'])
            except Exception as exc:
                state['failures'] = state.get('failures', 0) + 1
                state['produce_retry_after'] = now() + RETRY
                state['last_error'] = f'{type(exc).__name__}: {str(exc)[:300]}'
                log('StoryWobble production failed: ' + state['last_error'])
                if state['failures'] in (2, 6):_notify('failure', 'production', state['last_error'])
        state['last_tick_at'] = now()
        sp.save(STATE, state)
        return {'status': 'ok', 'published': published, 'produced': produced}


if __name__ == '__main__':
    print(json.dumps(tick(), ensure_ascii=False))
