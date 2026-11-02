#!/usr/bin/env python3
"""Channel-bound YouTube uploads with a durable resumable receipt."""
import fcntl
import hashlib
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request, AuthorizedSession
from browser_process import run as run_browser

BASE_DIR = Path(os.getenv('VIDEO_FACTORY_DIR', Path(__file__).resolve().parent.parent))
TOKEN_PATH = Path(os.getenv('YOUTUBE_TOKEN_PATH', BASE_DIR / 'youtube_token.json'))
PROXIES = {k: os.getenv(k.upper() + '_PROXY', 'http://127.0.0.1:17890') for k in ('http', 'https')}
API = 'https://www.googleapis.com/youtube/v3/'


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)


def get_session(token_path=None):
    path = Path(token_path or TOKEN_PATH)
    creds = Credentials.from_authorized_user_file(str(path))
    if not creds.valid:
        if not creds.refresh_token:
            raise RuntimeError('YouTube authorization requires renewal')
        req = Request()
        req.session.proxies = PROXIES
        creds.refresh(req)
        save(path, json.loads(creds.to_json()))
    session = AuthorizedSession(creds)
    session.proxies = PROXIES
    return session


def api_json(response):
    if not response.ok:
        # Do not log request URLs, tokens or upload session URLs.
        body = response.json() if 'json' in response.headers.get('content-type', '') else {}
        reasons = [e.get('reason') for e in body.get('error', {}).get('errors', [])]
        raise RuntimeError(f'YouTube HTTP {response.status_code}: {reasons}')
    return response.json()


def channel_identity(session):
    items = api_json(session.get(API + 'channels', params={'part': 'id,snippet', 'mine': 'true'}, timeout=30)).get('items', [])
    if len(items) != 1:
        raise RuntimeError('YouTube authorization must identify exactly one channel')
    return {'id': items[0]['id'], 'title': items[0]['snippet']['title']}


def verify_video(session, video_id, channel_id, privacy):
    items = api_json(session.get(API + 'videos', params={'part': 'snippet,status,contentDetails', 'id': video_id}, timeout=30)).get('items', [])
    if len(items) != 1:
        raise RuntimeError(f'Uploaded video {video_id} is not readable; do not upload again')
    video = items[0]
    if video['snippet']['channelId'] != channel_id or video['status']['privacyStatus'] != privacy:
        raise RuntimeError('Uploaded channel or privacy differs from requested publication')
    if video['status'].get('uploadStatus') in ('failed', 'rejected', 'deleted'):
        raise RuntimeError('YouTube rejected the video')
    return video


def youtube_text(text):
    """YouTube refuses angle brackets in titles and descriptions. Studio marks the description
    invalid and leaves Next disabled forever, which stalled a publication on 'Settings > Usage'."""
    return text.replace('<', '←').replace('>', '→')


def youtube_tags(tags):
    return [tag.replace('<', '').replace('>', '').strip() for tag in tags or [] if tag.replace('<', '').replace('>', '').strip()]


def upload_video_studio(file_path, title, description, tags, cfg, brand):
    path = Path(file_path).resolve()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    folder = BASE_DIR / 'output' / 'studio_jobs'
    folder.mkdir(exist_ok=True)
    job = folder / (cfg['youtube_channel_id'] + '_' + digest + '.json')
    payload = {'file': '/data/output/' + str(path.relative_to(BASE_DIR / 'output')),
               'title': youtube_text(title), 'description': youtube_text(description), 'tags': youtube_tags(tags),
               'channel_id': cfg['youtube_channel_id'], 'channel_title': cfg['youtube_channel_title'],
               'author_url': cfg['youtube_author_url'], 'brand': brand}
    if cfg.get('language', 'ru') != 'ru':payload['language'] = cfg['language']  # job files for Russian stay byte-identical
    job.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    job.chmod(0o644)
    result = run_browser(['docker', 'exec', 'video_factory_auth', 'python3', '-u',
        '/app/scripts/youtube_studio_uploader.py', '/data/output/studio_jobs/' + job.name],
        capture_output=True, text=True, timeout=900)
    if result.returncode:
        raise RuntimeError('YouTube Studio upload needs retry or session renewal: ' + result.stderr[-700:])
    return json.loads(result.stdout.strip().splitlines()[-1])


def upload_video(file_path, title, description, tags=None, privacy_status='public',
                 token_path=None, expected_channel_id=None, receipt_path=None, category_id='28'):
    path = Path(file_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    size = path.stat().st_size
    session = get_session(token_path)
    channel = channel_identity(session)
    expected = expected_channel_id or os.getenv('YOUTUBE_CHANNEL_ID', 'UCCDUe6_z1zDKQaBwQbxtv3g')
    if channel['id'] != expected:
        raise RuntimeError(f'Wrong YouTube channel: {channel["title"]} ({channel["id"]})')
    receipt_path = Path(receipt_path or BASE_DIR / 'data' / 'uploads' / f'{expected}_{digest}.json')
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with receipt_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        if state and (state['sha256'] != digest or state['channel_id'] != expected or state['privacy'] != privacy_status):
            raise RuntimeError('Upload receipt belongs to a different file, channel or privacy')
        if not state:
            state = {'sha256': digest, 'channel_id': expected, 'privacy': privacy_status, 'status': 'initiating'}
            save(receipt_path, state)
            metadata = {'snippet': {'title': youtube_text(title)[:100], 'description': youtube_text(description)[:5000],
                'tags': youtube_tags(tags), 'categoryId': category_id, 'defaultLanguage': 'ru', 'defaultAudioLanguage': 'ru'},
                'status': {'privacyStatus': privacy_status, 'selfDeclaredMadeForKids': False,
                           'containsSyntheticMedia': True}}
            response = session.post('https://www.googleapis.com/upload/youtube/v3/videos',
                params={'uploadType': 'resumable', 'part': 'snippet,status'}, json=metadata,
                headers={'X-Upload-Content-Length': str(size), 'X-Upload-Content-Type': 'video/mp4'}, timeout=30)
            if response.status_code not in (200, 201):
                api_json(response)
            location = response.headers.get('Location')
            if not location:
                raise RuntimeError('Upload initiation returned no resumable URL')
            state.update(status='uploading', upload_url=location)
            save(receipt_path, state)
        if not state.get('id'):
            if not state.get('upload_url'):
                raise RuntimeError('Upload initiation was interrupted; manual reconciliation required before retry')
            # Ask YouTube for the committed offset, including after a lost final response.
            response = session.put(state['upload_url'], data=b'', headers={'Content-Length': '0', 'Content-Range': f'bytes */{size}'}, timeout=30)
            if response.status_code == 308:
                offset = int(response.headers.get('Range', 'bytes=0--1').split('-')[-1]) + 1 if response.headers.get('Range') else 0
                with path.open('rb') as stream:
                    stream.seek(offset)
                    while offset < size:
                        chunk = stream.read(4 * 1024 * 1024)
                        response = session.put(state['upload_url'], data=chunk, headers={
                            'Content-Type': 'video/mp4', 'Content-Length': str(len(chunk)),
                            'Content-Range': f'bytes {offset}-{offset+len(chunk)-1}/{size}'}, timeout=90)
                        if response.status_code in (200, 201):
                            break
                        if response.status_code != 308:
                            raise RuntimeError(f'Upload interrupted: HTTP {response.status_code}; resume this receipt')
                        offset = int(response.headers['Range'].split('-')[-1]) + 1
                        stream.seek(offset)
            if response.status_code not in (200, 201):
                raise RuntimeError(f'Resumable upload returned HTTP {response.status_code}; receipt retained, no new upload')
            data = response.json()
            if not data.get('id'):
                raise RuntimeError('YouTube upload returned no video id')
            state.update(id=data['id'], status='uploaded', uploaded_at=datetime.now(timezone.utc).isoformat())
            state.pop('upload_url', None)
            save(receipt_path, state)  # Persist ID before any optional read-back can fail.
        video = verify_video(session, state['id'], expected, privacy_status)
        state.update(status='success', privacy_status=video['status']['privacyStatus'],
            upload_status=video['status']['uploadStatus'], title=video['snippet']['title'],
            published_at=video['snippet']['publishedAt'], url='https://youtu.be/' + state['id'],
            verified_at=datetime.now(timezone.utc).isoformat())
        save(receipt_path, state)
        return {k: v for k, v in state.items() if k != 'upload_url'}


if __name__ == '__main__':
    import sys
    print(json.dumps(upload_video(*sys.argv[1:4], privacy_status=sys.argv[4] if len(sys.argv)>4 else 'public'), ensure_ascii=False))
