#!/usr/bin/env python3
"""Source -> scene script -> new Flow clips -> Russian speech -> reviewable Shorts.

Generation saves a preview by default. Publishing requires --publish explicitly.
"""
import argparse
import base64
import fcntl
import hashlib
import html
import json
import os
import re
import sqlite3
import subprocess
import time
import uuid
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from script_generator import generate_shorts_script, validate_script, save_script, DB_PATH, PROXY_URL

BASE_DIR = Path(os.getenv('VIDEO_FACTORY_DIR', Path(__file__).resolve().parent.parent))
OUTPUT_DIR = BASE_DIR / 'output'
DATA_DIR = BASE_DIR / 'data'
TRACKING_FILE = DATA_DIR / 'processed_post_ids.json'
TTS_MODEL = 'gemini-3.1-flash-tts-preview'
RUSSIAN_VOICE = 'Orus'


def write_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(path)


def get_next_topic(post_id=None):
    if os.getenv('MEDIA_BRAND', 'wrongdev') == 'migratoria':
        from migratoria_editor import next_video_source
        return next_video_source(post_id)
    processed = set(json.loads(TRACKING_FILE.read_text())) if TRACKING_FILE.exists() else set()
    held_path=DATA_DIR/'review_held_video_sources.json'
    held=json.loads(held_path.read_text()).get('wrongdev',{}) if held_path.exists() else {}
    with sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True) as con:
        if post_id is not None:
            rows = con.execute("SELECT id,title,processed_text,original_text,posted_date FROM posts WHERE id=? AND status='posted'", (post_id,)).fetchall()
        else:
            rows = con.execute("SELECT id,title,processed_text,original_text,posted_date FROM posts WHERE status='posted' ORDER BY id DESC LIMIT 30").fetchall()
    ready = [{'id': pid, 'title': title, 'text': processed_text or original_text, 'posted': posted} for pid, title, processed_text, original_text, posted in rows
             if post_id is not None or (pid not in processed and str(pid) not in held)]
    if ready:
        # Ranking only reorders fresh sources: an old story never overtakes today's news because of its topic class.
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        fresh = [item for item in ready if (item['posted'] or '') >= cutoff[:19]]
        if post_id is None and len(fresh) > 1:
            from growth_engine import pick_source
            return pick_source('wrongdev', fresh[:8])
        return ready[0]
    raise RuntimeError('No unprocessed published source post; no synthetic or recycled topic will be used')


def container_path(path):
    return '/data/output/' + str(Path(path).resolve().relative_to(OUTPUT_DIR.resolve()))


def docker_command(*args):
    return subprocess.run(['docker', 'exec', 'video_factory_auth', *map(str, args)],
                          capture_output=True, text=True, check=True)


def probe(path):
    return json.loads(docker_command('ffprobe', '-v', 'error', '-show_streams',
                                    '-show_format', '-of', 'json', container_path(path)).stdout)


def create_voiceover_audio(text, audio_path):
    # Explicit language/delivery and transcript boundary; never use a US voice fallback.
    quota_file=DATA_DIR/'tts_quota.json'
    if quota_file.exists() and json.loads(quota_file.read_text()).get('retry_at',0)>time.time():
        raise RuntimeError('Russian TTS daily quota is exhausted; retry after the recorded provider reset')
    key = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
    if not key:
        raise RuntimeError('GEMINI_API_KEY is required for Russian Gemini TTS')
    direction = ('Synthesize speech in native Russian (ru-RU), confident conversational male '
        'speaker voice, natural Russian pronunciation, brisk and expressive without an English '
        'accent. Speak only the exact transcript, without introductions or extra words. Transcript:\n')
    response = None
    for attempt in range(3):
      try:
        response = requests.post(f'https://generativelanguage.googleapis.com/v1beta/models/{TTS_MODEL}:generateContent',
        headers={'x-goog-api-key': key},
        json={'contents': [{'parts': [{'text': direction + text}]}],
              'generationConfig': {'responseModalities': ['AUDIO'], 'speechConfig': {
                  'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': RUSSIAN_VOICE}}}}},
        proxies={'https': PROXY_URL}, timeout=90)
        if response.status_code==429:
            error=response.json().get('error',{})
            details=error.get('details',[])
            violations=[v for d in details for v in d.get('violations',[])]
            if any('PerDay' in v.get('quotaId','') for v in violations):
                delays=[float(d['retryDelay'].rstrip('s')) for d in details if d.get('retryDelay')]
                retry_at=time.time()+(max(delays) if delays else 86400)
                write_json(quota_file,{'status':'daily_quota_exhausted','model':TTS_MODEL,'retry_at':retry_at,
                    'quota_value':next((v.get('quotaValue') for v in violations if 'PerDay' in v.get('quotaId','')),None)})
                write_json(audio_path.with_suffix('.error.json'),{'http_status':429,'quota':details})
                raise RuntimeError('Russian TTS daily quota is exhausted; provider reset recorded, no replacement voice used')
        if response.status_code not in (429, 500, 502, 503, 504): break
      except requests.RequestException:
        if attempt == 2: raise RuntimeError('Russian TTS network connection failed after three attempts') from None
      time.sleep(3 * (attempt + 1))
    if response is None: raise RuntimeError('Russian TTS unavailable')
    if response.status_code != 200:
        error=response.json().get('error',{})
        detail=[d for d in error.get('details',[]) if d.get('@type','').endswith(('QuotaFailure','RetryInfo'))]
        write_json(audio_path.with_suffix('.error.json'),{'http_status':response.status_code,'status':error.get('status'),'quota':detail})
        raise RuntimeError(f'Russian Gemini TTS returned HTTP {response.status_code}; quota details saved; no replacement voice used')
    parts = [part for candidate in response.json().get('candidates', [])
             for part in candidate.get('content', {}).get('parts', []) if part.get('inlineData')]
    if len(parts) != 1:
        raise RuntimeError('Russian Gemini TTS did not return exactly one audio stream')
    audio = parts[0]['inlineData']
    if not audio['mimeType'].startswith('audio/l16'):
        raise RuntimeError('Unexpected TTS audio encoding')
    with wave.open(str(audio_path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(base64.b64decode(audio['data']))
    write_json(audio_path.with_suffix('.tts.json'), {'provider': 'gemini', 'model': TTS_MODEL,
        'voice': RUSSIAN_VOICE, 'language': 'ru-RU', 'transcript': text})
    if not audio_path.exists() or audio_path.stat().st_size < 1000:
        raise RuntimeError('Russian speech synthesis did not produce audio')
    duration = float(probe(audio_path)['format']['duration'])
    if duration > 7.8:
        raise RuntimeError(f'Speech segment lasts {duration:.2f}s; shorten it before generating an eight-second clip')
    return duration


def generate_flow_video_file(video_prompt, title, run_id, resume=False, voiceover_ru=None):
    receipt = OUTPUT_DIR / f'flow_{run_id}.json'
    if resume and receipt.exists():
        previous=json.loads(receipt.read_text())
        if previous.get('status') in ('error','cancelled_before_submission') and not (previous.get('submitted_at') or previous.get('submission_attempted_at')):
            if run_id.count('_retry')>=2:raise RuntimeError('Flow connection failed before submission three times; no video request sent')
            return generate_flow_video_file(video_prompt,title,run_id+'_retry1',resume=True,voiceover_ru=voiceover_ru)
    if receipt.exists() and not resume:
        raise RuntimeError('Generation receipt already exists; refusing to use a previous result')
    command = ['docker', 'exec', 'video_factory_auth', 'python3', '-u', '/app/scripts/flow_automator.py',
               video_prompt, title, '--run-id', run_id]
    if voiceover_ru:command += ['--speech-ru', voiceover_ru,'--model','Veo 3.1 - Fast']
    existing = json.loads(receipt.read_text()) if receipt.exists() else None
    if existing and existing.get('status') != 'completed':
        command.append('--resume-download')
    if not existing or existing.get('status') != 'completed':
        from browser_process import run as run_browser
        result = run_browser(command, text=True, timeout=900)
        if result.returncode or not receipt.exists():
            raise RuntimeError('Flow did not complete this generation request')
    data = json.loads(receipt.read_text())
    expected_hash = hashlib.sha256(video_prompt.encode()).hexdigest()
    if (data.get('status') != 'completed' or data.get('run_id') != run_id or
            data.get('prompt_sha256') != expected_hash or data.get('voiceover_ru') != voiceover_ru or not data.get('media_id') or
            data['media_id'] in data.get('baseline_media_ids', [])):
        raise RuntimeError('Flow receipt does not prove a new clip for this scene')
    path = OUTPUT_DIR / Path(data['video_file']).relative_to('/data/output')
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != data['sha256']:
        raise RuntimeError('Generated clip is missing or differs from its receipt')
    return path, data


def ass_time(value):
    return f'0:{int(value // 60):02d}:{value % 60:05.2f}'


def ass_text(text):
    return text.replace('\\', '').replace('{', '').replace('}', '').replace('\n', ' ')


def scene_subtitles(scene, duration, path):
    header = '''[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Label,Liberation Sans,48,&H00FFFFFF,&H000000FF,&H00101010,&H00101010,-1,0,0,0,100,100,0,0,3,12,0,8,55,55,150,1
Style: Speech,Liberation Sans,42,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,55,55,260,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    label=scene['on_screen_text_ru'];detail=scene.get('on_screen_detail_ru')
    # One editorial card at a time. Keep the subject visible and the CTA uncluttered.
    split=min(1.5,duration/2) if detail and '@' not in label else duration
    events=[f'Dialogue: 0,0:00:00.00,{ass_time(split)},Label,,0,0,0,,{ass_text(label)}']
    if split<duration:
        events.append(f'Dialogue: 0,{ass_time(split)},{ass_time(duration)},Label,,0,0,0,,{ass_text(detail)}')
    timed=scene.get('_speech_words')
    if timed:
        for i in range(0,len(timed),3):
            chunk=timed[i:i+3]
            events.append(f"Dialogue: 1,{ass_time(chunk[0]['start'])},{ass_time(chunk[-1]['end'])},Speech,,0,0,0,,{ass_text(' '.join(w['word'] for w in chunk))}")
    else:
        words=scene['voiceover_ru'].split()
        chunks=[' '.join(words[i:i+3]) for i in range(0,len(words),3)]
        for index,chunk in enumerate(chunks):
            start,end=duration*index/len(chunks),duration*(index+1)/len(chunks)
            events.append(f'Dialogue: 1,{ass_time(start)},{ass_time(end)},Speech,,0,0,0,,{ass_text(chunk)}')
    path.write_text(header + '\n'.join(events) + '\n')


def render_scene(raw, audio, subtitles, duration, target):
    raw_duration = float(probe(raw)['format']['duration'])
    if raw_duration + 0.05 < duration:
        raise RuntimeError('New clip is shorter than its narration; looping and freezing are forbidden')
    docker_command('ffmpeg', '-v', 'error', '-y', '-i', container_path(raw), '-i', container_path(audio),
        '-vf', f'scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,fps=24,subtitles={container_path(subtitles)}',
        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-preset', 'fast', '-crf', '19',
        '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-b:a', '160k',
        '-t', f'{duration:.3f}', container_path(target))
    return target


def run_pipeline(custom_topic=None, post_id=None, script_json=None, publish=False, resume=None, job_id=None):
    audio_mode=os.getenv('MEDIA_AUDIO_MODE','gemini')
    if audio_mode not in ('gemini','native','vids'):raise ValueError('Unknown audio mode')
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if resume and not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', resume):
        raise ValueError('Invalid run id')
    run_id = resume or job_id or (time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
    run_dir = OUTPUT_DIR / 'runs' / run_id
    if resume:
        manifest = json.loads((run_dir / 'manifest.json').read_text())
        if manifest['run_id'] != resume or manifest['status'] in ('preview_ready', 'published'):
            raise RuntimeError('Only an unfinished run can be resumed')
        post_id = manifest.get('source_post_id')
        script_json = run_dir / 'script.json'
        if not script_json.exists():
            if manifest.get('source_sha256') or manifest.get('scenes') or post_id is None:
                raise RuntimeError('Cannot resume a missing script after generation or without a persisted source')
            script_json = None
        elif post_id is None:
            custom_topic = json.loads(script_json.read_text())['source_text']
        manifest.update(status='resuming', resumed_at=time.time(), scenes=[], publish_requested=publish)
        manifest.pop('error', None)
    else:
        run_dir.mkdir(parents=True)
        manifest = {'run_id': run_id, 'status': 'started', 'started_at': time.time(),
                    'voice': RUSSIAN_VOICE, 'tts_model': TTS_MODEL,
                    'publish_requested': publish, 'scenes': [], 'brand': os.getenv('MEDIA_BRAND', 'wrongdev')}
    manifest['audio_mode']=audio_mode
    if audio_mode=='native':manifest.update(voice='Veo native ru-RU',tts_model=None)
    if audio_mode=='vids':manifest.update(voice='Google Vids ru-RU',tts_model='Included Google Vids voiceover')
    manifest_path = run_dir / 'manifest.json'
    write_json(manifest_path, manifest)
    with (DATA_DIR / '.pipeline.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            topic = ({'id': None, 'title': custom_topic[:70], 'text': custom_topic} if custom_topic
                     else get_next_topic(post_id))
            manifest['source_post_id'] = topic['id']
            if script_json:
                script = validate_script(json.loads(Path(script_json).read_text()))
                source = html.unescape(re.sub(r'<[^>]+>', '', topic['text']))
                if script.get('source_sha256') != hashlib.sha256(source.encode()).hexdigest():
                    raise RuntimeError('The supplied script belongs to a different source post')
                save_script(script, run_dir)
            else:
                script = generate_shorts_script(topic['text'], run_dir)
            manifest['source_sha256'] = script.get('source_sha256')
            manifest['title'] = script['youtube_meta']['title']
            from script_review import audit,require_pass as require_script_pass
            script_review=audit(script,run_dir)
            require_script_pass(script_review)
            manifest['script_review']=script_review
            # Synthesize every segment first: reject scripts that cannot fit, before using video credits.
            prepared = []
            if audio_mode=='vids':
                from bundled_voiceover import prepare
                prepared=prepare(script,run_dir,run_id)
                manifest['voice']='Google Vids '+script.get('narration_voice','Holt')+' ru-RU'
                manifest['script_review']=audit(script,run_dir)
                require_script_pass(manifest['script_review'])
                manifest['narration_revision']=script.get('narration_revision',0)
            for index, scene in enumerate(script['scenes'], 1):
                if audio_mode=='vids':continue
                if audio_mode=='native':
                    prepared.append((scene,None,None));continue
                audio = run_dir / f'scene_{index:02d}.wav'
                metadata_file = audio.with_suffix('.tts.json')
                metadata = json.loads(metadata_file.read_text()) if metadata_file.exists() else {}
                if (resume and audio.exists() and audio.stat().st_size > 1000
                        and metadata.get('transcript') == scene['voiceover_ru']
                        and metadata.get('model') == TTS_MODEL and metadata.get('voice') == RUSSIAN_VOICE):
                    duration = float(probe(audio)['format']['duration'])
                    if duration > 7.8:
                        raise RuntimeError('Prepared narration exceeds the clip duration')
                else:
                    duration = create_voiceover_audio(scene['voiceover_ru'], audio)
                prepared.append((scene, audio, duration))
            for index, (scene, audio, duration) in enumerate(prepared, 1):
                manifest['status'] = f'generating_scene_{index}'
                write_json(manifest_path, manifest)
                asset_run=f'{run_id}_native_s{index}' if audio_mode=='native' else f'{run_id}_s{index}'
                from video_provider_pool import generate_video_file
                raw, receipt = generate_video_file(scene['video_prompt_en'], manifest['title'], asset_run,
                    resume=bool(resume),voiceover_ru=scene['voiceover_ru'] if audio_mode=='native' else None)
                subtitles = run_dir / f'scene_{index:02d}.ass'
                target = run_dir / f'scene_{index:02d}.mp4'
                if audio_mode=='native':
                    from native_audio import analyze,render
                    start,end,asr=analyze(raw,scene,run_dir,index)
                    duration=end-start
                    scene_subtitles(scene,duration,subtitles)
                    render(raw,subtitles,start,end,target)
                else:
                    scene_subtitles(scene, duration, subtitles)
                    render_scene(raw, audio, subtitles, duration, target)
                manifest['scenes'].append({'id': index, 'raw_video': str(raw), 'media_id': receipt['media_id'],
                    'sha256': receipt['sha256'], 'duration': duration, 'rendered_video': str(target),
                    'voiceover_ru': scene['voiceover_ru'], 'video_prompt_en': scene['video_prompt_en']})
                write_json(manifest_path, manifest)
            clips = [Path(s['rendered_video']) for s in manifest['scenes']]
            concat = run_dir / 'concat.txt'
            concat.write_text(''.join(f"file '{container_path(p)}'\n" for p in clips))
            final = run_dir / 'short.mp4'
            docker_command('ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i',
                           container_path(concat), '-c', 'copy', '-movflags', '+faststart', container_path(final))
            details = probe(final)
            actual = float(details['format']['duration'])
            expected = sum(s['duration'] for s in manifest['scenes'])
            if abs(actual - expected) > 0.35 or not any(s['codec_type'] == 'audio' for s in details['streams']):
                raise RuntimeError('Final video duration or audio does not match the scene plan')
            if len({s['media_id'] for s in manifest['scenes']}) != len(clips):
                raise RuntimeError('A generated asset was reused between scenes')
            if len({s['sha256'] for s in manifest['scenes']}) != len(clips):
                raise RuntimeError('A generated video file was repeated between scenes')
            from video_quality import inspect,require_pass
            quality=inspect(final,script,manifest['scenes'],run_dir/'visual_review.json')
            require_pass(quality,len(clips))
            manifest['visual_review']=quality
            manifest.update(status='preview_ready', video=str(final), duration=actual,
                            sha256=hashlib.sha256(final.read_bytes()).hexdigest())
            write_json(manifest_path, manifest)
            if publish:
                from youtube_uploader import upload_video,upload_video_studio
                from video_quality import require_artifact
                require_artifact(manifest)
                meta = script['youtube_meta']
                cfg=json.loads((DATA_DIR/'media_brands.json').read_text())[manifest['brand']]
                if cfg.get('youtube_uploader')=='studio':
                    uploaded=upload_video_studio(str(final),meta['title'],meta['description'],meta['tags'],cfg,manifest['brand'])
                else:
                    uploaded=upload_video(str(final),title=meta['title'],description=meta['description'],tags=meta['tags'],
                        privacy_status='public',token_path=cfg['youtube_token'],
                        expected_channel_id=cfg['youtube_channel_id'],category_id=cfg.get('category_id','28'))
                if uploaded.get('status') != 'success':
                    raise RuntimeError('YouTube upload did not report success')
                manifest.update(status='published', youtube_url=uploaded.get('url'))
            if topic['id'] is not None and os.getenv('MEDIA_BRAND', 'wrongdev') == 'wrongdev':
                processed = set(json.loads(TRACKING_FILE.read_text())) if TRACKING_FILE.exists() else set()
                processed.add(topic['id'])
                write_json(TRACKING_FILE, sorted(processed))
            manifest['completed_at'] = time.time()
            write_json(manifest_path, manifest)
            for suffix in ('json', 'md'):
                (OUTPUT_DIR / f'latest_script.{suffix}').write_text((run_dir / f'script.{suffix}').read_text())
            print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
            return manifest
        except Exception as exc:
            manifest.update(status='error', error=str(exc))
            write_json(manifest_path, manifest)
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('topic', nargs='?')
    parser.add_argument('--post-id', type=int)
    parser.add_argument('--script-json', type=Path)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--resume', help='Continue only this unfinished job and its correlated assets')
    args = parser.parse_args()
    run_pipeline(args.topic, args.post_id, args.script_json, args.publish, args.resume)
