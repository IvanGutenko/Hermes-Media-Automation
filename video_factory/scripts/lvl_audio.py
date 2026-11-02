"""Original game-style sound effects and music loops for the LVL series, synthesised with the standard library.

Nothing here is sampled or downloaded, so there is no licence to track. Files are generated once into
assets/lvl/audio and reused; every loop is deterministic for its (key, bpm) pair."""
import math
import random
import wave
from array import array
from pathlib import Path

RATE = 22050
AUDIO_DIR = Path(__file__).resolve().parent.parent / 'assets' / 'lvl' / 'audio'
NOTE = {n: i for i, n in enumerate(('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'))}
KEYS = {'A_minor': ('A', (0, 3, 5, 7, 10)), 'C_major': ('C', (0, 2, 4, 7, 9)), 'D_minor': ('D', (0, 3, 5, 7, 10)),
        'G_major': ('G', (0, 2, 4, 7, 9)), 'E_minor': ('E', (0, 3, 5, 7, 10)), 'F_major': ('F', (0, 2, 4, 7, 9))}


def midi_freq(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def _write(path, samples):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = max(1e-9, max(abs(s) for s in samples))
    data = array('h', (int(max(-1, min(1, s / peak * 0.9)) * 32767) for s in samples))
    tmp = path.with_suffix('.tmp')
    with wave.open(str(tmp), 'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(RATE);w.writeframes(data.tobytes())
    tmp.replace(path)
    return path


def _square(freq, n, duty=0.5, decay=6.0, start=0.0):
    out = []
    for i in range(n):
        t = i / RATE
        phase = (freq * t) % 1.0
        out.append((1.0 if phase < duty else -1.0) * math.exp(-decay * t))
    return out


def _tri(freq, n, decay=3.0):
    return [(abs(((freq * i / RATE) % 1.0) * 4 - 2) - 1) * math.exp(-decay * i / RATE) for i in range(n)]


def _noise(n, decay=30.0, seed=1):
    rnd = random.Random(seed)
    return [(rnd.random() * 2 - 1) * math.exp(-decay * i / RATE) for i in range(n)]


def _mix(buf, chunk, at, gain):
    for i, v in enumerate(chunk):
        j = at + i
        if j >= len(buf):break
        buf[j] += v * gain


def sfx(name):
    """Path of a short effect (pop, ding, whoosh, levelup, buzz, tick), generated on first use."""
    path = AUDIO_DIR / f'sfx_{name}.wav'
    if path.exists():return path
    s = []
    if name == 'pop':
        n = int(RATE * 0.16)
        s = [math.sin(2 * math.pi * (300 + 900 * (i / n)) * i / RATE) * math.exp(-18 * i / RATE) for i in range(n)]
    elif name == 'ding':
        n = int(RATE * 0.5)
        s = [(math.sin(2 * math.pi * 988 * i / RATE) + 0.6 * math.sin(2 * math.pi * 1319 * i / RATE)) * math.exp(-7 * i / RATE) for i in range(n)]
    elif name == 'whoosh':
        n = int(RATE * 0.35)
        rnd = random.Random(7);lp = 0.0;s = []
        for i in range(n):
            k = i / n
            lp += (rnd.random() * 2 - 1 - lp) * (0.05 + 0.5 * math.sin(math.pi * k) ** 2)
            s.append(lp * math.sin(math.pi * k))
    elif name == 'levelup':
        s = [0.0] * int(RATE * 1.1)
        for k, m in enumerate((72, 76, 79, 84, 88)):
            _mix(s, _square(midi_freq(m), int(RATE * 0.3), 0.25, 5), int(RATE * 0.1 * k), 0.5)
        _mix(s, _tri(midi_freq(60), int(RATE * 0.9), 3), 0, 0.8)
    elif name == 'buzz':
        n = int(RATE * 0.3)
        s = [(1.0 if (110 * i / RATE) % 1 < 0.5 else -1.0) * 0.6 * math.exp(-5 * i / RATE) for i in range(n)]
    elif name == 'tick':
        s = _noise(int(RATE * 0.04), 90, 3)
    else:
        raise ValueError('unknown effect ' + name)
    return _write(path, s)


def music(key='A_minor', bpm=104, seconds=32.0):
    """Looping chiptune bed (square arpeggio, triangle bass, noise hats). Cached by key and tempo."""
    path = AUDIO_DIR / f'music_{key}_{bpm}.wav'
    if path.exists():return path
    root_name, scale = KEYS[key]
    root = 48 + NOTE[root_name]
    beat = 60.0 / bpm
    step = beat / 2  # eighth notes
    total = int(RATE * seconds)
    buf = [0.0] * total
    rnd = random.Random(f'{key}:{bpm}')
    progression = [0, -4, -7, -2] if 'minor' in key else [0, -3, -5, -7]  # root moves per bar
    steps = int(seconds / step)
    for i in range(steps):
        bar = i // 8
        move = progression[bar % len(progression)]
        pos = i % 8
        at = int(RATE * i * step)
        if pos in (0, 4):
            _mix(buf, _tri(midi_freq(root - 12 + move), int(RATE * step * 1.8), 2.5), at, 0.9)
        degree = scale[rnd.choice((0, 1, 2, 2, 3, 4))] + (12 if rnd.random() < 0.4 else 0)
        _mix(buf, _square(midi_freq(root + 12 + move + degree), int(RATE * step * 0.9), 0.25, 9), at, 0.35)
        if pos % 2 == 0:
            _mix(buf, _noise(int(RATE * 0.05), 70, i), at, 0.22 if pos % 4 else 0.35)
        if pos in (0, 4):
            _mix(buf, [math.sin(2 * math.pi * (110 - 60 * k / (RATE * 0.12)) * k / RATE) * math.exp(-25 * k / RATE) for k in range(int(RATE * 0.14))], at, 0.9)
    # fade ends so the loop does not click
    fade = int(RATE * 0.02)
    for i in range(fade):
        buf[i] *= i / fade;buf[-1 - i] *= i / fade
    return _write(path, buf)


def ensure_assets():
    for name in ('pop', 'ding', 'whoosh', 'levelup', 'buzz', 'tick'):sfx(name)
    for key, bpm in (('A_minor', 104), ('C_major', 112), ('D_minor', 98), ('G_major', 108)):music(key, bpm)
