"""Renders an LVL episode: fixed 2D characters, prepared animations, karaoke captions, RPG interface, sound.

Input is a list of prepared scenes (see `Scene` fields below); output is a 1080x1920 H.264/AAC mp4.
Characters are never regenerated per video: sprites come from a spec and are cached by quantised pose."""
import array
import collections
import json
import math
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from PIL import Image
import lvl_art
import lvl_audio
import lvl_scene
import lvl_voice

W, H = lvl_scene.W, lvl_scene.H
FPS = 24
PAD = 0.28            # silence after each line
INTRO = 0.9           # title card before the first line
OUTRO = 2.4           # end card
CHAR_Y = 1010          # sprite centre
CAPTION_Y = 1450
TITLE_Y = 620          # title card centre: above the cast's faces (they start near y=1000)
SIDES = {'center': (540, 940), 'left': (290, 720), 'right': (790, 720)}
_sprite_cache = collections.OrderedDict()
_sprite_bytes = 0
SPRITE_BUDGET = 120 * 2 ** 20    # a 940 px RGBA sprite is 3.5 MB; the 4.9 GB host also runs the browser (about 2 GB) and ffmpeg (about 0.2 GB with the lean x264 settings; 0.65 GB without them)
_icon_cache = {}
_bg_cache = collections.OrderedDict()
BG_ENTRIES = 12


def sprite(spec, pose, size):
    global _sprite_bytes
    key = (json.dumps(spec, sort_keys=True), pose.get('expression', 'neutral'), round(pose.get('mouth', 0) * 4) / 4, pose.get('blink', 0) > 0.5,
        round(pose.get('arm_l', 20) / 12), round(pose.get('arm_r', 20) / 12), round(pose.get('squash', 1) * 25), round(pose.get('tilt', 0) / 2),
        round(pose.get('xp', 0.6) * 10), pose.get('level', 1), size)
    hit = _sprite_cache.get(key)
    if hit is not None:
        _sprite_cache.move_to_end(key)
        return hit
    q = dict(pose)
    q['mouth'] = round(pose.get('mouth', 0) * 4) / 4
    q['blink'] = 1.0 if pose.get('blink', 0) > 0.5 else 0.0
    q['arm_l'] = round(pose.get('arm_l', 20) / 12) * 12
    q['arm_r'] = round(pose.get('arm_r', 20) / 12) * 12
    q['squash'] = round(pose.get('squash', 1) * 25) / 25
    q['tilt'] = round(pose.get('tilt', 0) / 2) * 2
    q['xp'] = round(pose.get('xp', 0.6) * 10) / 10
    hit = lvl_art.draw_character(spec, q, size)
    _sprite_cache[key] = hit
    _sprite_bytes += hit.width * hit.height * 4
    while _sprite_bytes > SPRITE_BUDGET and len(_sprite_cache) > 1:
        _, old = _sprite_cache.popitem(last=False)
        _sprite_bytes -= old.width * old.height * 4
    return hit


def icon(name, size):
    key = (name, size)
    if key not in _icon_cache:
        layer = Image.new('RGBA', (int(size * 1.3), int(size * 1.3)), (0, 0, 0, 0))
        lvl_scene.draw_icon(layer, name, layer.width // 2, layer.height // 2, size)
        _icon_cache[key] = layer
    return _icon_cache[key]


def background(setting, seed):
    key = (setting, seed % 4)
    if key in _bg_cache:
        _bg_cache.move_to_end(key)
        return _bg_cache[key]
    _bg_cache[key] = lvl_scene.background(setting, seed % 4).convert('RGBA')
    while len(_bg_cache) > BG_ENTRIES:_bg_cache.popitem(last=False)
    return _bg_cache[key]


def paste_center(canvas, im, cx, cy, scale=1.0, angle=0.0):
    if scale <= 0.02:return
    if scale != 1.0:im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.BILINEAR)
    if angle:im = im.rotate(angle, resample=Image.BILINEAR, expand=True)
    canvas.paste(im, (int(cx - im.width / 2), int(cy - im.height / 2)), im)


def action_motion(action, t, since):
    """(dx, dy, squash, tilt, arm_l, arm_r) for a named action at time t seconds into the scene."""
    dx = dy = tilt = 0.0
    squash = 1.0 + 0.018 * math.sin(t * 5.2)
    arm_l = 24 + 10 * math.sin(t * 4.1)
    arm_r = 24 + 10 * math.sin(t * 4.1 + 1.7)
    dy += 9 * math.sin(t * 5.2)
    if action == 'bounce':
        k = abs(math.sin(t * 7.0))
        dy -= 70 * k;squash = 1.0 + 0.1 * (0.5 - k)
    elif action == 'shake':
        dx += 16 * math.sin(t * 38);tilt = 3 * math.sin(t * 31)
    elif action == 'hop' and since < 0.5:
        k = since / 0.5
        dy -= 150 * math.sin(math.pi * k);squash = 1.0 + 0.12 * math.cos(math.pi * k * 2)
    elif action == 'cheer':
        arm_l = arm_r = 150 + 18 * math.sin(t * 12)
        dy -= 30 * abs(math.sin(t * 6))
    elif action == 'slump':
        dy += 40;squash = 0.93;tilt = -4;arm_l = arm_r = 8
    elif action == 'point':
        arm_r = 105
    elif action == 'spin':
        tilt = 12 * math.sin(t * 9)
    elif action == 'flex':
        arm_l = arm_r = 120 + 10 * math.sin(t * 8);squash = 1.04
    return dx, dy, squash, tilt, arm_l, arm_r


def blink(t, phase):
    x = (t + phase) % 3.2
    return 1.0 if x < 0.13 else 0.0


def draw_actor(canvas, actor, t, since, line_mouth, line_active):
    cx, size = SIDES.get(actor.get('side', 'center'), SIDES['center'])
    inset=actor.get('_footage_inset',False)
    if inset:size=int(size*.42)
    appear = lvl_scene.pop_scale(min(1.0, since / 0.28 + 0.0001))
    dx, dy, squash, tilt, al, ar = action_motion(actor.get('action', 'none'), t, since)
    speaking = actor.get('speaking') and line_active
    pose = {'expression': actor.get('expression', 'neutral'), 'mouth': line_mouth if speaking else 0.0,
        'blink': blink(t, actor.get('phase', 0.0)), 'arm_l': al, 'arm_r': ar, 'squash': squash, 'tilt': tilt,
        'xp': actor.get('xp', 0.6), 'level': actor.get('level', 1)}
    if speaking:pose['look'] = (0, 0)
    im = sprite(actor['spec'], pose, size)
    paste_center(canvas, im, cx + dx*(.42 if inset else 1), (1220 if inset else CHAR_Y) + dy*(.42 if inset else 1) + (0 if inset or actor.get('side', 'center') == 'center' else 90), appear)


@lru_cache(maxsize=1)
def footage_scrim():
    strip=Image.new('RGBA',(1,H))
    strip.putdata([(12,10,28,int(max(0,1-y/370)*165+max(0,min(1,(y-1190)/310))*190)) for y in range(H)])
    return strip.resize((W,H))


def props_layer(canvas, props, t):
    for p in props:
        since = t - p['at']
        if since < 0:continue
        k = lvl_scene.pop_scale(min(1.0, since / 0.3))
        bob = 12 * math.sin((t + p['at']) * 3.3)
        paste_center(canvas, icon(p['icon'], p.get('size', 230)), p['x'] * W, p['y'] * H + bob, k)


def ease_flash(canvas, k):
    """Short white flash on cuts; k in 0..1."""
    if 0 < k < 1:
        flash = Image.new('RGBA', canvas.size, (255, 255, 255, int(110 * (1 - k))))
        canvas.alpha_composite(flash)


@lru_cache(maxsize=64)
def fit_size(text, size, family, weight, max_w=960, min_size=28):
    """Largest font size (<= size) at which `text` still fits `max_w` pixels, so long translations never run off the frame."""
    while size > min_size and lvl_art.font(size, family, weight).getlength(text) > max_w:size -= 2
    return size


def fit_font(text, size, family, weight, max_w=960):
    return lvl_art.font(fit_size(text, size, family, weight, max_w), family, weight)


def title_card(canvas, title, subtitle, k):
    from PIL import ImageDraw
    d = ImageDraw.Draw(canvas)
    s = lvl_scene.pop_scale(min(1.0, k * 3))
    fnt = lvl_art.font(int(70 * max(0.4, s)), 'Unbounded', 900)
    lines, line = [], []
    for w in title.split():
        if d.textlength(' '.join(line + [w]), font=fnt) > 840 and line:
            lines.append(' '.join(line));line = [w]
        else:line.append(w)
    lines.append(' '.join(line))
    lines = lines[:3]
    top = TITLE_Y - 45 * (len(lines) - 1) - 90
    bottom = TITLE_Y + 45 * (len(lines) - 1) + 90 + (60 if subtitle else 0)
    d.rounded_rectangle([60, top, 1020, bottom], radius=60, fill=(30, 27, 75, 235), outline=(167, 139, 250, 255), width=8)
    y = TITLE_Y - 45 * (len(lines) - 1)
    for ln in lines:
        d.text((540, y), ln, font=fnt, fill='#ffffff', anchor='mm', stroke_width=5, stroke_fill='#1e1b4b');y += 90
    if subtitle:d.text((540, y + 10), subtitle, font=lvl_art.font(36, 'Nunito', 800), fill='#c4b5fd', anchor='mm')


def end_card(canvas, lines, k, hero_spec, t):
    from PIL import ImageDraw
    d = ImageDraw.Draw(canvas)
    panel = Image.new('RGBA', canvas.size, (15, 23, 42, int(200 * min(1, k * 4))))
    canvas.alpha_composite(panel)
    s = lvl_scene.pop_scale(min(1.0, k * 3))
    im = sprite(hero_spec, {'expression': 'proud', 'mouth': 0.0, 'arm_l': 140, 'arm_r': 140, 'squash': 1.0 + 0.02 * math.sin(t * 6), 'xp': 1.0, 'level': 1}, 600)
    paste_center(canvas, im, 540, 820, s)
    y = 1250
    for i, ln in enumerate(lines):
        fnt = fit_font(ln, 64 if i == 0 else 44, 'Unbounded', 900 if i == 0 else 700)
        d.text((540, y), ln, font=fnt, fill='#fde047' if i == 0 else '#e9d5ff', anchor='mm', stroke_width=5, stroke_fill='#1e1b4b');y += 92


def timeline(scenes, lang_gap=PAD):
    """Start time of every scene on the global clock; returns (starts, total_seconds)."""
    t = INTRO
    starts = []
    for sc in scenes:
        starts.append(t)
        t += sc['duration'] + lang_gap
    return starts, t + OUTRO


def build_audio(scenes, starts, total, music_key, music_bpm, extra_sfx=()):
    n = int(total * lvl_audio.RATE)
    mix = [0.0] * n
    # music bed, ducked under speech
    bed = wave_samples(lvl_audio.music(music_key, music_bpm))
    for i in range(n):
        mix[i] = bed[i % len(bed)] * 0.16
    for sc, st in zip(scenes, starts):
        base = int(st * lvl_audio.RATE)
        for i, v in enumerate(sc['samples']):
            j = base + i
            if j >= n:break
            mix[j] += v / 32768.0
            # duck the music while the voice plays
            mix[j] -= bed[j % len(bed)] * 0.09
    for name, at, gain in extra_sfx:
        fx = wave_samples(lvl_audio.sfx(name))
        base = int(at * lvl_audio.RATE)
        for i, v in enumerate(fx):
            if base + i >= n:break
            mix[base + i] += v * gain
    peak = max(1e-9, max(abs(v) for v in mix))
    scale = 0.92 / peak if peak > 0.92 else 1.0
    return array.array('h', (int(max(-1, min(1, v * scale)) * 32767) for v in mix))


def wave_samples(path):
    import wave
    with wave.open(str(path), 'rb') as w:
        raw = array.array('h')
        raw.frombytes(w.readframes(w.getnframes()))
    return [v / 32768.0 for v in raw]


def sfx_plan(scenes, starts):
    plan = []
    for i, (sc, st) in enumerate(zip(scenes, starts)):
        plan.append(('whoosh', max(0.0, st - 0.12), 0.45 if i else 0.3))
        for p in sc.get('props', []):
            plan.append(('pop', st + p['at'], 0.5))
        if sc.get('popup'):
            plan.append(('ding', st + sc.get('popup_at', 0.5), 0.55))
        if sc.get('level_up'):
            plan.append(('levelup', st + sc.get('popup_at', 0.5), 0.7))
        if sc.get('buzz'):
            plan.append(('buzz', st + sc.get('buzz_at', 0.4), 0.5))
    return plan


def mouth_at(sc, local_t):
    env = sc.get('envelope') or []
    i = int(local_t * FPS)
    return env[i] if 0 <= i < len(env) else 0.0


def active_word(sc, local_t):
    idx = -1
    for i, w in enumerate(sc['words']):
        if w['start'] <= local_t + 0.02:idx = i
    return idx


def caption_state(sc, local_t):
    """(phrase_words, active_index_within_phrase) for the caption chunk currently being spoken, or ([], -1)."""
    texts = [w['text'] for w in sc['words']]
    chunks = lvl_scene.chunk_words(texts)
    cur = active_word(sc, local_t)
    if local_t > sc['duration'] + 0.1 or cur < 0:
        return ([], -1) if cur < 0 else (chunks[-1][1], len(chunks[-1][1]))
    for start, ws in chunks:
        if start <= cur < start + len(ws):
            return ws, cur - start
    return [], -1


def scene_at(scenes, starts, total, t):
    """(index, local_time) of the scene covering time t, or (None, t) for title/outro."""
    for i in range(len(scenes) - 1, -1, -1):
        if t >= starts[i]:
            end = starts[i] + scenes[i]['duration'] + PAD
            return (i, t - starts[i]) if t < end else (None, t)
    return None, t


def render_frame(scenes, starts, total, t, ctx):
    idx, local = scene_at(scenes, starts, total, t)
    outro = t >= total - OUTRO
    if idx is None:
        nxt = next((i for i, st in enumerate(starts) if st > t), None)
        sc = scenes[0] if (nxt == 0 or t < starts[0]) else scenes[-1] if nxt is None else scenes[nxt - 1]
        show = sc
        local_t = local if idx is not None else (t - starts[0] if t >= starts[0] else 0.0)
    else:
        show = scenes[idx];local_t = local
    footage=ctx['footage'].frame(idx,local) if ctx.get('footage') else None
    canvas = footage if footage is not None else background(show['setting'], show.get('seed', 0)).copy()
    if footage is not None:canvas.alpha_composite(footage_scrim())
    if footage is None and idx is not None and local < 0.2 and idx > 0:
        prev = background(scenes[idx - 1]['setting'], scenes[idx - 1].get('seed', 0))
        canvas = Image.blend(prev, canvas, local / 0.2)
    line_active = idx is not None
    mouth = mouth_at(show, local_t) if line_active else 0.0
    if not outro:  # the end card brings its own hero; the last scene's cast must not ghost behind it
        if footage is None:props_layer(canvas, show.get('props', []), local_t if idx is not None else 99)
        for actor in show['actors']:
            a = dict(actor)
            if footage is not None:a['_footage_inset']=True
            a['xp'] = ctx['xp_at'](t);a['level'] = ctx['level_at'](t)
            draw_actor(canvas, a, t, local_t if idx is not None else 5.0, mouth, line_active)
    if show.get('popup') and idx is not None:
        k = (local_t - show.get('popup_at', 0.5)) / 1.4
        lvl_scene.xp_popup(canvas, show['popup'], 540, 330 if show.get('level_up') else 520, k)
    if show.get('level_up') and idx is not None:
        lvl_scene.level_up(canvas, (local_t - show.get('popup_at', 0.5)) / 1.6, ctx['level_at'](t))
    lvl_scene.hud(canvas, ctx['xp_at'](t), ctx['level_at'](t), ctx['name'], ctx.get('title'))
    if t < INTRO:
        title_card(canvas, ctx['title_text'], ctx.get('subtitle'), t / INTRO)
    elif idx is not None:
        words, active = caption_state(show, local_t)
        lvl_scene.caption(canvas, words, active, y=CAPTION_Y)
        ease_flash(canvas, local_t / 0.12 if idx > 0 else 2)
    if outro:
        end_card(canvas, ctx['end_lines'], (t - (total - OUTRO)) / OUTRO, ctx['hero_spec'], t)
    return canvas.convert('RGB')


def make_progress(scenes, starts, total, level, xp_start):
    """XP bar fills as +XP popups fire; returns (xp_at, level_at) closures."""
    gains = []
    for sc, st in zip(scenes, starts):
        if sc.get('xp_gain'):gains.append((st + sc.get('popup_at', 0.5), float(sc['xp_gain'])))
        if sc.get('level_up'):gains.append((st + sc.get('popup_at', 0.5), 1.0 - xp_start))

    def xp_at(t):
        xp = xp_start
        for at, g in gains:
            if t >= at:xp = min(1.0, xp + g * min(1.0, (t - at) / 0.8))
        return xp if xp < 1.0 else (xp % 1.0 or 1.0)

    def level_at(t):
        lv = level
        for sc, st in zip(scenes, starts):
            if sc.get('level_up') and t >= st + sc.get('popup_at', 0.5) + 0.2:lv += 1
        return lv

    return xp_at, level_at


def render(scenes, out_path, work_dir, title, subtitle=None, end_lines=('Subscribe for next level',), hero_spec=None, level=1, xp_start=0.35,
        name='LVL', music=('A_minor', 104), progress=None):
    """Render the episode to out_path. Returns {'duration', 'frames', 'seconds'}."""
    started = time.time()
    out_path = Path(out_path);work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    starts, total = timeline(scenes)
    xp_at, level_at = make_progress(scenes, starts, total, level, xp_start)
    ctx = {'xp_at': xp_at, 'level_at': level_at, 'name': name, 'title_text': title, 'subtitle': subtitle, 'end_lines': list(end_lines),
        'hero_spec': hero_spec or scenes[0]['actors'][0]['spec']}
    from lvl_hybrid import Frames
    footage=Frames(scenes);ctx['footage']=footage
    pcm = build_audio(scenes, starts, total, music[0], music[1], sfx_plan(scenes, starts))
    wav = lvl_voice.write_wav(work_dir / 'mix.wav', pcm)
    frames = int(total * FPS)
    # No '-shortest': with a whole audio file available ffmpeg then buffers video and audio and grows to ~960 MB (measured; 220 MB with '-t').
    cmd = lvl_voice.ffmpeg_cmd('ffmpeg', '-y', '-v', 'error', '-framerate', str(FPS), '-f', 'image2pipe', '-c:v', 'mjpeg', '-i', 'pipe:0',
        '-i', lvl_voice.runtime_path(wav), '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', '-threads', '2', '-x264-params', 'rc-lookahead=8:ref=2:bframes=1:sync-lookahead=0',
        '-pix_fmt', 'yuv420p', '-r', str(FPS), '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-t', f'{total:.3f}', '-movflags', '+faststart',
        lvl_voice.runtime_path(out_path), interactive=True)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for n in range(frames):
            frame = render_frame(scenes, starts, total, n / FPS, ctx)
            buf = __import__('io').BytesIO()
            frame.save(buf, 'JPEG', quality=90)
            proc.stdin.write(buf.getvalue())
            if progress and n % 48 == 0:progress(n, frames)
        proc.stdin.close()
        err = proc.stderr.read().decode('utf-8', 'replace')
        if proc.wait(timeout=300) != 0:raise RuntimeError('ffmpeg failed: ' + err[-300:])
    except BaseException:
        proc.kill()
        raise
    finally:
        footage.close()
        proc.stderr.close()
    return {'duration': total, 'frames': frames, 'seconds': round(time.time() - started, 1)}
