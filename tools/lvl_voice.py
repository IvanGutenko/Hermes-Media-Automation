"""Character voices for the LVL series: Edge neural TTS with per-word timings, plus a mouth-movement envelope.

Every character has its own voice, pitch and pace per language, so a cast of cartoon characters sounds like one.
Audio is decoded through ffmpeg, which lives inside the browser container on the server and locally elsewhere."""
import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import wave
from array import array
from pathlib import Path

PROXY = os.getenv('LVL_TTS_PROXY', os.getenv('HTTPS_PROXY') or os.getenv('HTTP_PROXY') or '')
RATE = 22050
CONTAINER = 'video_factory_auth'
TICKS = 10_000_000  # edge-tts reports offsets in 100 ns ticks

# voice, pitch (Hz offset), rate (%), volume (%) per language
VOICES = {
    'hero': {'ru': ('ru-RU-DmitryNeural', 22, 6, 0), 'en': ('en-US-GuyNeural', 22, 6, 0)},
    'lazy': {'ru': ('ru-RU-DmitryNeural', -14, -16, 0), 'en': ('en-US-ChristopherNeural', -14, -16, 0)},
    'spark': {'ru': ('ru-RU-SvetlanaNeural', 38, 14, 0), 'en': ('en-US-AnaNeural', 20, 12, 0)},
    'sly': {'ru': ('ru-RU-SvetlanaNeural', -6, -8, 0), 'en': ('en-US-AriaNeural', -6, -8, 0)},
    'boss': {'ru': ('ru-RU-DmitryNeural', -26, -4, 0), 'en': ('en-GB-RyanNeural', -12, -4, 0)},
    'narrator': {'ru': ('ru-RU-DmitryNeural', 0, 0, 0), 'en': ('en-US-GuyNeural', 0, 0, 0)},
}
SPEAKERS = tuple(VOICES)


class VoiceError(RuntimeError):
    pass


def voice_for(kind, lang):
    table = VOICES.get(kind) or VOICES['narrator']
    return table[lang]


_IN_CONTAINER = None
OUTPUT_DIR = Path(os.getenv('VIDEO_FACTORY_DIR', Path(__file__).resolve().parent.parent)) / 'output'


def in_container():
    """True when ffmpeg should run inside the browser container (the server); False for a local binary."""
    global _IN_CONTAINER
    if _IN_CONTAINER is None:
        _IN_CONTAINER = False
        if os.getenv('LVL_FFMPEG_MODE') != 'local' and shutil.which('docker'):
            try:_IN_CONTAINER = subprocess.run(['docker', 'exec', CONTAINER, 'true'], capture_output=True, timeout=15).returncode == 0
            except (subprocess.TimeoutExpired, OSError):pass
    return _IN_CONTAINER


def ffmpeg_cmd(*args, interactive=False):
    """Command running ffmpeg/ffprobe where it exists: the browser container on the server, else the local binary."""
    if in_container():
        return ['docker', 'exec', *(['-i'] if interactive else []), CONTAINER, *args]
    if not shutil.which(args[0]):
        raise VoiceError(f'{args[0]} is not available locally or in the container')
    return list(args)


def runtime_path(path):
    """Path of a file under output/ as the ffmpeg process sees it."""
    if not in_container():return str(path)
    return '/data/output/' + str(Path(path).resolve().relative_to(OUTPUT_DIR.resolve()))


def decode_pcm(mp3_bytes, rate=RATE, timeout=120):
    """mp3 bytes -> signed 16-bit mono samples at `rate`."""
    cmd = ffmpeg_cmd('ffmpeg', '-v', 'error', '-i', 'pipe:0', '-f', 's16le', '-ac', '1', '-ar', str(rate), 'pipe:1', interactive=True)
    done = subprocess.run(cmd, input=mp3_bytes, capture_output=True, timeout=timeout)
    if done.returncode != 0 or not done.stdout:
        raise VoiceError('ffmpeg could not decode the voice: ' + done.stderr.decode('utf-8', 'replace')[-200:])
    samples = array('h')
    samples.frombytes(done.stdout[:len(done.stdout) // 2 * 2])
    return samples


def write_wav(path, samples, rate=RATE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate);w.writeframes(array('h', samples).tobytes())
    return path


def trim_silence(samples, rate=RATE, threshold=350, pad=0.06):
    """Cut leading/trailing silence so scenes join tightly; returns (samples, lead_seconds_removed)."""
    n = len(samples)
    a = 0
    while a < n and abs(samples[a]) < threshold:a += 1
    b = n
    while b > a and abs(samples[b - 1]) < threshold:b -= 1
    p = int(pad * rate)
    a2, b2 = max(0, a - p), min(n, b + p)
    return samples[a2:b2], a2 / rate


def envelope(samples, fps=24, rate=RATE):
    """Mouth openness per video frame, 0..1, from short-window RMS (smoothed, with a floor so quiet speech still moves)."""
    win = max(1, rate // fps)
    values = []
    for i in range(0, len(samples), win):
        chunk = samples[i:i + win]
        if not chunk:break
        values.append((sum(s * s for s in chunk) / len(chunk)) ** 0.5)
    if not values:return []
    ceiling = sorted(values)[int(len(values) * 0.92)] or max(values) or 1.0
    out = []
    prev = 0.0
    for v in values:
        target = min(1.0, v / ceiling)
        prev = prev * 0.45 + target * 0.55
        out.append(round(prev if prev > 0.08 else 0.0, 3))
    return out


def _clean(text):
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:raise VoiceError('empty line')
    return text


async def _stream(text, voice, pitch, rate, volume, proxy):
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate=f'{rate:+d}%', volume=f'{volume:+d}%', pitch=f'{pitch:+d}Hz',
        boundary='WordBoundary', **({'proxy': proxy} if proxy else {}))
    audio = bytearray();words = []
    async for chunk in comm.stream():
        if chunk['type'] == 'audio':audio.extend(chunk['data'])
        elif chunk['type'] == 'WordBoundary':
            words.append({'text': chunk['text'], 'start': chunk['offset'] / TICKS, 'end': (chunk['offset'] + chunk['duration']) / TICKS})
    if not audio:raise VoiceError('no audio received')
    return bytes(audio), words


def speak(text, kind, lang, attempts=7, proxy=None):
    """Synthesise one line. Returns {'samples', 'words', 'duration', 'voice'} with leading silence removed from the timings."""
    text = _clean(text)
    voice, pitch, rate, volume = voice_for(kind, lang)
    last = None
    for attempt in range(attempts):
        try:
            audio, words = asyncio.run(asyncio.wait_for(_stream(text, voice, pitch, rate, volume, PROXY if proxy is None else proxy), 90))
            samples = decode_pcm(audio)
            trimmed, lead = trim_silence(samples)
            shift = lead
            fixed = [{'text': w['text'], 'start': max(0.0, w['start'] - shift), 'end': max(0.0, w['end'] - shift)} for w in words]
            duration = len(trimmed) / RATE
            if duration < 0.3:raise VoiceError('voice line is nearly silent')
            return {'samples': trimmed, 'words': fixed, 'duration': duration, 'voice': voice}
        except Exception as exc:  # network, 403 token handshake, decoding: all worth a retry with backoff
            last = exc
            if attempt + 1 < attempts:time.sleep(min(12.0, 2.0 * (attempt + 1)))  # the service answers 'no audio' in bursts; a longer pause clears it
    raise VoiceError(f'voice synthesis failed for {voice}: {type(last).__name__}: {last}')


def dump_words(words):
    return json.dumps([{**w, 'start': round(w['start'], 3), 'end': round(w['end'], 3)} for w in words], ensure_ascii=False)
