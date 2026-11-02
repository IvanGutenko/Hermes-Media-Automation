"""Produces one LVL episode in both languages: topic -> evidence -> script -> voices -> render -> quality gates.

Output lives in output/lvl_runs/<run_id>/ with manifest.json (status 'ready' once both videos pass the gates).
Publishing is a separate step (lvl_worker), so a rendered episode survives any platform outage."""
import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
import lvl_cast
import lvl_render
import lvl_script
import lvl_topics
import lvl_voice

BASE_DIR = Path(os.getenv('VIDEO_FACTORY_DIR', Path(__file__).resolve().parent.parent))
RUNS = BASE_DIR / 'output' / 'lvl_runs'
LOCK = BASE_DIR / 'data' / 'lvl' / '.produce.lock'
LANGS = ('ru', 'en')
MIN_SECONDS, MAX_SECONDS = 24.0, 58.0
MAX_RENDER_ATTEMPTS = 3          # a script whose voices or render fail is finished later, this many times
RESUME_WINDOW = 12 * 3600
END_LINES = {'ru': ('Следующий уровень скоро', 'Подписывайся, чтобы не пропустить'), 'en': ('Next level soon', 'Follow so you do not miss it')}
SUBTITLE = {'ru': 'ЛВЛ · твоя жизнь как RPG', 'en': 'LVL · your life as an RPG'}
MUSIC = (('A_minor', 104), ('C_major', 112), ('D_minor', 98), ('G_major', 108))


class ProduceError(RuntimeError):
    pass


class HostBusy(ProduceError):
    """The server has no memory to spare for a render: try again later (not a failure of the episode)."""


MIN_AVAILABLE_MIB = 1500
MAX_MEMORY_PRESSURE = 25.0     # percent of the last 60 s during which some task waited for memory (/proc/pressure/memory)


def host_headroom():
    """(available MiB, memory pressure avg60) as far as /proc tells; unknown values come back as None."""
    avail = pressure = None
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):avail = int(line.split()[1]) // 1024
    except (OSError, ValueError):pass
    try:
        some = Path('/proc/pressure/memory').read_text().splitlines()[0]
        pressure = float(re.search(r'avg60=([\d.]+)', some).group(1))
    except (OSError, ValueError, AttributeError, IndexError):pass
    return avail, pressure


def check_host(headroom=None):
    avail, pressure = (headroom or host_headroom)()
    if avail is not None and avail < MIN_AVAILABLE_MIB:raise HostBusy(f'host busy: {avail} MiB available, need {MIN_AVAILABLE_MIB}')
    if pressure is not None and pressure > MAX_MEMORY_PRESSURE:raise HostBusy(f'host busy: memory pressure {pressure}% over the last minute')


def slug(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:36] or 'ep'


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1))
    tmp.replace(path)


def probe(path):
    out = subprocess.run(lvl_voice.ffmpeg_cmd('ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', lvl_voice.runtime_path(path)),
        capture_output=True, text=True, timeout=60)
    if out.returncode:raise ProduceError('ffprobe failed: ' + out.stderr[-200:])
    return json.loads(out.stdout)


def gate(path, expected_seconds):
    """Quality gates on the finished file; raises ProduceError."""
    info = probe(path)
    streams = {s['codec_type']: s for s in info['streams']}
    if 'video' not in streams or 'audio' not in streams:raise ProduceError('file has no video or no audio stream')
    duration = float(info['format']['duration'])
    if not MIN_SECONDS <= duration <= MAX_SECONDS:raise ProduceError(f'duration {duration:.1f}s outside {MIN_SECONDS}-{MAX_SECONDS}s')
    if abs(duration - expected_seconds) > 1.5:raise ProduceError(f'duration {duration:.1f}s differs from the timeline {expected_seconds:.1f}s')
    if (streams['video']['width'], streams['video']['height']) != (1080, 1920):raise ProduceError('frame size is not 1080x1920')
    out = subprocess.run(lvl_voice.ffmpeg_cmd('ffmpeg', '-v', 'info', '-i', lvl_voice.runtime_path(path), '-af', 'volumedetect', '-vn', '-f', 'null', '-'),
        capture_output=True, text=True, timeout=120)
    m = re.search(r'mean_volume: (-?[\d.]+) dB', out.stderr)
    if not m or float(m.group(1)) < -38:raise ProduceError('audio is silent or far too quiet')
    if path.stat().st_size > 60 * 2 ** 20:raise ProduceError('file larger than 60 MB')
    return {'duration': round(duration, 2), 'bytes': path.stat().st_size, 'mean_volume_db': float(m.group(1))}


def collect_evidence(topic):
    items, errors = lvl_topics.evidence(topic['evidence_query_en'], topic['rubric'])
    if topic.get('broad_query_en'):
        more, more_errors = lvl_topics.evidence(topic['broad_query_en'], topic['rubric'])
        titles = {i['title'] for i in items}
        items = (items + [m for m in more if m['title'] not in titles])[:7]
        errors = {**errors, **more_errors}
    items.sort(key=lambda i: -i.get('relevance', 0))
    for n, it in enumerate(items, 1):it['id'] = f'e{n}'
    return items, errors


def render_scenes(episode, cast, lang):
    """Voice every line and assemble render scenes for one language."""
    scenes = []
    for i, sc in enumerate(episode['scenes']):
        speaker = cast[sc['speaker']]
        voice = lvl_voice.speak(sc['text_' + lang], speaker['voice'], lang)
        scenes.append({'setting': sc['setting'], 'seed': i, 'samples': voice['samples'], 'words': voice['words'], 'duration': voice['duration'],
            'envelope': lvl_voice.envelope(voice['samples']),
            'actors': [{'spec': cast[a['id']]['spec'], 'side': a['side'], 'expression': a['expression'], 'action': a['action'],
                'speaking': a['id'] == sc['speaker'], 'phase': (sum(map(ord, a['id'])) % 10) / 10} for a in sc['actors']],
            'props': [{**p, 'at': min(p['at'], max(0.3, voice['duration'] - 0.5))} for p in sc['props']],
            'popup': sc['popup'], 'popup_at': min(1.0, max(0.4, voice['duration'] * 0.5)), 'xp_gain': 0.3 if sc['popup'] else 0, 'level_up': sc['level_up']})
    return scenes


def episode_number():
    """Counts finished episodes only: failed runs must not move the level and XP story forward."""
    done = 0
    for p in RUNS.glob('*/manifest.json'):
        try:done += json.loads(p.read_text()).get('status') in ('ready', 'published')
        except (OSError, ValueError):pass
    return done + 1


def find_resumable(now=None):
    """Newest run whose script passed every check but whose voices or render failed (TTS outage, ffmpeg): finish it instead of writing a new one."""
    now = now or time.time()
    for p in sorted(RUNS.glob('*/manifest.json'), reverse=True)[:8]:
        try:m = json.loads(p.read_text())
        except (OSError, ValueError):continue
        # 'rendering' while we hold the production lock means the process that was rendering it died (killed, host restarted): same as a failure
        stamp = m.get('failed_at') if m.get('status') == 'failed' else m.get('resumed_at') or m.get('started_at')
        interrupted = m.get('status') == 'rendering' or (m.get('status') == 'failed' and m.get('resumable'))
        if interrupted and m.get('attempts', 1) < MAX_RENDER_ATTEMPTS and now - (stamp or 0) < RESUME_WINDOW and (p.parent / 'script.json').exists():
            return p.parent, m
    return None


def write_new_episode(rubric, log):
    topic, evidence, errors, tried, episode, rejected = None, [], {}, [], None, []
    for _ in range(3):
        topic = lvl_script.pick_topic(rubric, exclude=tried)
        tried.append(topic['phrase'])
        log('topic: ' + topic['title_en'] + ' [' + topic['phrase'] + ']')
        evidence, errors = collect_evidence(topic)
        if len([e for e in evidence if e['relevance'] >= 0.6 or e['kind'] == 'official guidance']) < 2:
            log('too little relevant evidence for this topic, choosing another')
            continue
        cast = lvl_cast.load()
        try:
            episode = lvl_script.write_episode(topic, evidence, cast)
            break
        except lvl_script.ScriptError as exc:  # the checker held the script to its sources: another topic may have better ones
            rejected.append(f"{topic['phrase']}: {str(exc)[:200]}")
            log('script rejected, choosing another topic: ' + rejected[-1])
    if episode is None:
        raise ProduceError('no topic produced an acceptable episode; tried: ' + '; '.join(tried) + ('; rejected: ' + ' | '.join(rejected) if rejected else ' (too little relevant evidence)'))
    extra = {episode['guest']: cast[episode['guest']]} if episode.get('guest') else {}  # a guest lives only in memory until the episode is rendered
    return topic, episode, errors, extra


def render_languages(run, manifest, episode, cast, langs, log=print):
    """Voices + video for the given languages; records path, gate and render time in the manifest."""
    number = manifest['episode']
    level = 1 + (number - 1) // 4
    music = MUSIC[number % len(MUSIC)]
    for lang in langs:
        scenes = render_scenes(episode, cast, lang)
        out = run / lang / 'short.mp4'
        result = lvl_render.render(scenes, out, run / lang, episode['title_' + lang], SUBTITLE[lang], END_LINES[lang], cast[lvl_cast.HERO]['spec'],
            level=level, xp_start=0.2 + 0.2 * ((number - 1) % 4), name='LVL', music=music)
        manifest['videos'][lang] = str(out)
        manifest['gates'][lang] = gate(out, result['duration'])
        manifest['gates'][lang]['render_seconds'] = result['seconds']
        log(f'{lang}: {manifest["gates"][lang]}')
        (run / lang / 'mix.wav').unlink(missing_ok=True)


def rerender(run_id, langs, log=print):
    """Render chosen languages of a READY episode again from its (edited) script.json, e.g. after a wording rule changed.
    Languages already published keep their receipts; only the video files of `langs` are replaced."""
    run = RUNS / run_id
    manifest = json.loads((run / 'manifest.json').read_text())
    if manifest.get('status') != 'ready':raise ProduceError(f'{run_id} is {manifest.get("status")}, only ready episodes are re-rendered')
    saved = json.loads((run / 'script.json').read_text())
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a') as lock:
        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:raise ProduceError('another LVL production is running')
        check_host()
        cast = {**lvl_cast.load(), **(saved.get('cast_extra') or {})}
        render_languages(run, manifest, saved['episode'], cast, langs, log)
        manifest['rerendered'] = {**manifest.get('rerendered', {}), **{lang: time.time() for lang in langs}}
        manifest['sha256'] = {l: hashlib.sha256(Path(p).read_bytes()).hexdigest() for l, p in manifest['videos'].items()}
        save(run / 'manifest.json', manifest)
        return manifest


def produce(rubric=None, log=print, force=False):
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a') as lock:
        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:raise ProduceError('another LVL production is running')
        if not force:check_host()   # force: the worker waited hours for a calm host; the encoder is lean now, so render anyway
        started = time.time()
        resumed = find_resumable()
        if resumed:
            run, manifest = resumed
            saved = json.loads((run / 'script.json').read_text())
            topic, episode, errors, extra = saved['topic'], saved['episode'], saved.get('evidence_errors', {}), saved.get('cast_extra') or {}
            manifest.pop('error', None)
            manifest.update(status='rendering', attempts=manifest.get('attempts', 1) + 1, resumed_at=started, videos={}, gates={})
            log(f"resuming {manifest['run_id']} (attempt {manifest['attempts']}): {topic['title_en']}")
        else:
            topic, episode, errors, extra = write_new_episode(rubric, log)
            number = episode_number()
            base_id = f"lvl-{time.strftime('%Y%m%d-%H%M%S')}-{slug(topic['title_en'])}"
            run_id, n = base_id, 1
            while (RUNS / run_id).exists():
                n += 1;run_id = f'{base_id}-{n}'
            run = RUNS / run_id
            run.mkdir(parents=True)
            manifest = {'run_id': run_id, 'brand': 'lvl', 'status': 'rendering', 'started_at': started, 'episode': number, 'rubric': topic['rubric'], 'attempts': 1,
                'topic': {k: topic[k] for k in ('phrase', 'title_en', 'title_ru', 'evidence_query_en', 'angle')}, 'videos': {}, 'gates': {}}
            save(run / 'script.json', {'topic': topic, 'episode': episode, 'evidence_errors': errors, 'cast_extra': extra})
        cast = {**lvl_cast.load(), **extra}
        number = manifest['episode']
        run_id = manifest['run_id']
        save(run / 'manifest.json', manifest)
        try:
            render_languages(run, manifest, episode, cast, LANGS, log)
        except BaseException as exc:
            # Voices, ffmpeg and the speech service fail for reasons unrelated to the script, so the finished script is kept and tried again later.
            manifest.update(status='failed', error=f'{type(exc).__name__}: {exc}'[:300], failed_at=time.time(), resumable=not isinstance(exc, (KeyboardInterrupt, SystemExit)))
            save(run / 'manifest.json', manifest)
            raise
        manifest.pop('resumable', None)
        manifest.update(status='ready', completed_at=time.time(), sha256={l: hashlib.sha256(Path(p).read_bytes()).hexdigest() for l, p in manifest['videos'].items()})
        save(run / 'manifest.json', manifest)
        lvl_topics.record({'title_en': episode['title_en'], 'title_ru': episode['title_ru'], 'summary': episode['summary'], 'rubric': topic['rubric'], 'phrase': topic['phrase'], 'run_id': run_id})
        lvl_cast.mark_used(cast, {a['id'] for sc in episode['scenes'] for a in sc['actors']})
        return manifest


if __name__ == '__main__':
    import sys
    if sys.argv[1:2] == ['rerender']:   # lvl_pipeline.py rerender <run_id> <lang> [<lang>]
        print(json.dumps({'run_id': sys.argv[2], 'gates': rerender(sys.argv[2], sys.argv[3:])['gates']}, ensure_ascii=False));sys.exit(0)
    m = produce(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({'run_id': m['run_id'], 'status': m['status'], 'gates': m['gates']}, ensure_ascii=False))
