"""Procedural 2D cartoon characters for the LVL series (Pillow only, no generated video).

A character is a small JSON spec (body shape, colours, eyes, accessory, chest panel). The same spec always
draws the same character, so appearance never drifts between episodes; only the pose changes. This is the
fixed-design, prepared-animation approach the owner asked for instead of regenerating a character per clip.

Pose keys (all optional): expression, mouth (0..1 speech opening), blink (0..1), arm_l / arm_r (degrees,
0 hanging down, 90 out to the side, 180 straight up), squash (1 neutral, <1 squashed), tilt (degrees),
look (dx, dy in -1..1), xp (0..1 for the chest bar), level (int shown on the chest panel).
"""
import functools
import math
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFont

SS = 2  # supersampling factor for smooth edges
FONT_DIR = Path(__file__).resolve().parent.parent / 'assets' / 'lvl' / 'fonts'
FONT_FALLBACKS = ('/usr/share/fonts/liberation/LiberationSans-Bold.ttf', '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
                  '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf', '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf')
SHAPES = ('squircle', 'circle', 'capsule', 'drop', 'cloud', 'battery', 'coin', 'blob')
ACCESSORIES = ('none', 'antenna', 'horns', 'cat_ears', 'cap', 'glasses', 'headband', 'crown', 'halo', 'sprout')
EYE_STYLES = ('round', 'dot', 'visor', 'big')
EXPRESSIONS = ('neutral', 'happy', 'excited', 'shocked', 'tired', 'sad', 'angry', 'thinking', 'smug', 'scared', 'proud')

# expression: (brow_tilt, brow_raise, lid, mouth_kind, extra)
EXPRESSION_SPECS = {
    'neutral': (0, 0.0, 0.0, 'smile', None),
    'happy': (-6, 0.25, 0.0, 'grin', 'sparkle'),
    'excited': (-10, 0.5, 0.0, 'open', 'sparkle'),
    'shocked': (-8, 0.7, 0.0, 'o', 'exclaim'),
    'tired': (14, -0.2, 0.55, 'wavy', 'zzz'),
    'sad': (18, 0.0, 0.2, 'frown', 'tear'),
    'angry': (-22, -0.15, 0.1, 'grit', 'anger'),
    'thinking': (-4, 0.3, 0.05, 'side', 'question'),
    'smug': (-8, 0.1, 0.35, 'smirk', None),
    'scared': (16, 0.55, 0.0, 'wobble', 'sweat'),
    'proud': (-4, 0.2, 0.0, 'grin', 'sparkle'),
}


@functools.lru_cache(maxsize=64)
def font(size, family='Rubik', weight=800):
    names = {'Rubik': 'Rubik.ttf', 'Unbounded': 'Unbounded.ttf', 'Nunito': 'Nunito.ttf'}
    path = FONT_DIR / names.get(family, 'Rubik.ttf')
    try:
        f = ImageFont.truetype(str(path), size)
        try:f.set_variation_by_axes([weight])
        except Exception:pass
        return f
    except OSError:
        for fallback in FONT_FALLBACKS:
            try:return ImageFont.truetype(fallback, size)
            except OSError:continue
    return ImageFont.load_default()


def hex_rgb(value, alpha=255):
    value = value.lstrip('#')
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)


def shade(color, factor):
    """Lighter (factor>1) or darker (<1) version of an RGBA/RGB tuple."""
    r, g, b = color[:3]
    if factor >= 1:
        return (min(255, int(r + (255 - r) * (factor - 1))), min(255, int(g + (255 - g) * (factor - 1))), min(255, int(b + (255 - b) * (factor - 1))), 255)
    return (int(r * factor), int(g * factor), int(b * factor), 255)


def default_spec(**over):
    spec = {'id': 'lvl', 'name': 'LVL', 'shape': 'squircle', 'body': '#8b5cf6', 'accent': '#22c55e', 'outline': '#2a1650',
            'belly': '#c4b5fd', 'eyes': 'round', 'eye_color': '#1f1147', 'accessory': 'none', 'accessory_color': '#facc15',
            'chest': 'xp', 'cheeks': '#f472b6', 'width': 1.0, 'height': 1.0, 'arms': True, 'legs': True}
    spec.update(over)
    return spec


def clean_spec(spec):
    """Validated copy of an LLM- or hand-written spec: unknown values fall back to safe defaults."""
    base = default_spec()
    out = dict(base)
    for key, value in (spec or {}).items():
        if key in base:out[key] = value
    if out['shape'] not in SHAPES:out['shape'] = 'squircle'
    if out['eyes'] not in EYE_STYLES:out['eyes'] = 'round'
    if out['accessory'] not in ACCESSORIES:out['accessory'] = 'none'
    if out['chest'] not in ('xp', 'none', 'heart', 'bolt'):out['chest'] = 'none'
    for key in ('body', 'accent', 'outline', 'belly', 'eye_color', 'accessory_color', 'cheeks'):
        try:hex_rgb(str(out[key]))
        except (ValueError, IndexError, TypeError):out[key] = base[key]
    for key in ('width', 'height'):
        try:out[key] = max(0.7, min(1.35, float(out[key])))
        except (TypeError, ValueError):out[key] = 1.0
    out['name'] = str(out.get('name') or 'X')[:24]
    out['id'] = ''.join(c for c in str(out.get('id') or out['name']).lower() if c.isalnum() or c in '_-')[:32] or 'x'
    out['arms'] = bool(out['arms']);out['legs'] = bool(out['legs'])
    return out


def _poly_mask(size, points):
    mask = Image.new('L', size, 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return mask


def body_mask(shape, size, cx, cy, bw, bh):
    """L-mode mask of the body silhouette; (cx,cy) centre, bw/bh half extents in pixels."""
    mask = Image.new('L', size, 0)
    d = ImageDraw.Draw(mask)
    if shape in ('squircle', 'battery'):
        d.rounded_rectangle([cx - bw, cy - bh, cx + bw, cy + bh], radius=min(bw, bh) * 0.55, fill=255)
    elif shape in ('circle', 'coin'):
        d.ellipse([cx - bw, cy - bh, cx + bw, cy + bh], fill=255)
    elif shape == 'capsule':
        d.rounded_rectangle([cx - bw * 0.86, cy - bh, cx + bw * 0.86, cy + bh], radius=bw * 0.86, fill=255)
    elif shape == 'drop':
        pts = []
        for i in range(120):
            t = 2 * math.pi * i / 120
            pts.append((cx + bw * 1.15 * math.sin(t) * (math.sin(t / 2) ** 0.9), cy - bh * math.cos(t) * 0.98))
        d.polygon(pts, fill=255)
    elif shape == 'cloud':
        d.rounded_rectangle([cx - bw, cy - bh * 0.1, cx + bw, cy + bh], radius=bh * 0.45, fill=255)
        for ox, oy, r in ((-0.55, -0.35, 0.55), (0.0, -0.62, 0.68), (0.55, -0.3, 0.55)):
            d.ellipse([cx + ox * bw - r * bw, cy + oy * bh - r * bh * 0.85, cx + ox * bw + r * bw, cy + oy * bh + r * bh * 0.85], fill=255)
    elif shape == 'blob':
        pts = []
        for i in range(120):
            t = 2 * math.pi * i / 120
            r = 1 + 0.07 * math.sin(3 * t + 0.6) + 0.04 * math.sin(5 * t)
            pts.append((cx + bw * r * math.cos(t), cy + bh * r * math.sin(t)))
        d.polygon(pts, fill=255)
    return mask


def _vertical_gradient(size, top, bottom):
    w, h = size
    strip = Image.new('RGBA', (1, h))
    for y in range(h):
        k = y / max(1, h - 1)
        strip.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * k) for i in range(3)) + (255,))
    return strip.resize((w, h))


def _line(d, p, q, width, fill):
    d.line([p, q], fill=fill, width=int(width))
    r = width / 2
    for x, y in (p, q):d.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def _star(d, cx, cy, r, fill):
    pts = []
    for i in range(8):
        a = math.pi / 4 * i - math.pi / 2
        rr = r if i % 2 == 0 else r * 0.38
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d.polygon(pts, fill=fill)


def draw_character(spec, pose=None, size=640, t=0.0):
    """RGBA sprite (size x size) of the character centred in the image, feet near the bottom."""
    spec = clean_spec(spec)
    pose = pose or {}
    expr = pose.get('expression', 'neutral')
    brow_tilt, brow_raise, lid_expr, mouth_kind, extra = EXPRESSION_SPECS.get(expr, EXPRESSION_SPECS['neutral'])
    squash = float(pose.get('squash', 1.0))
    N = size * SS
    img = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    unit = N * 0.27  # half body width in pixels at width=1
    bw, bh = unit * spec['width'] / math.sqrt(squash), unit * 1.02 * spec['height'] * squash
    cx = N / 2
    cy = N * 0.5 - (1 - squash) * unit * 0.2 - unit * 0.05
    body = hex_rgb(spec['body'])
    outline = hex_rgb(spec['outline'])
    accent = hex_rgb(spec['accent'])
    lw = max(2, int(unit * 0.06))

    arm_w = unit * 0.2
    def limb(angle_deg, side):
        # shoulder on the body side, arm swings in the vertical plane
        a = math.radians(angle_deg)
        sx, sy = cx + side * bw * 0.96, cy + bh * 0.12
        length = unit * 0.62
        ex, ey = sx + side * math.sin(a) * length, sy + math.cos(a) * length
        return (sx, sy), (ex, ey)

    # legs and shadow-free base
    if spec['legs']:
        for side in (-1, 1):
            lx = cx + side * bw * 0.42
            top = cy + bh * 0.82
            d.rounded_rectangle([lx - unit * 0.14 - lw, top - lw, lx + unit * 0.14 + lw, top + unit * 0.36 + lw], radius=unit * 0.12, fill=outline)
            d.rounded_rectangle([lx - unit * 0.14, top, lx + unit * 0.14, top + unit * 0.36], radius=unit * 0.1, fill=shade(body, 0.8))
            d.ellipse([lx - unit * 0.24 - lw, top + unit * 0.24 - lw, lx + unit * 0.24 + lw, top + unit * 0.46 + lw], fill=outline)
            d.ellipse([lx - unit * 0.24, top + unit * 0.24, lx + unit * 0.24, top + unit * 0.46], fill=shade(body, 0.7))

    arms = []
    if spec['arms']:
        arms = [limb(pose.get('arm_l', 20), -1), limb(pose.get('arm_r', 20), 1)]
        for (s, e) in arms:
            _line(d, s, e, arm_w + lw * 2, outline)
        for (s, e) in arms:
            _line(d, s, e, arm_w, shade(body, 0.92))
        for (s, e) in arms:
            r = arm_w * 0.85
            d.ellipse([e[0] - r - lw, e[1] - r - lw, e[0] + r + lw, e[1] + r + lw], fill=outline)
            d.ellipse([e[0] - r, e[1] - r, e[0] + r, e[1] + r], fill=shade(body, 1.12))

    # accessories behind the body
    acc, acc_color = spec['accessory'], hex_rgb(spec['accessory_color'])
    top_y = cy - bh
    if acc == 'antenna':
        _line(d, (cx, top_y + lw), (cx + unit * 0.08, top_y - unit * 0.42), unit * 0.08 + lw, outline)
        _line(d, (cx, top_y + lw), (cx + unit * 0.08, top_y - unit * 0.42), unit * 0.08, shade(body, 0.7))
        r = unit * 0.17
        ax, ay = cx + unit * 0.08, top_y - unit * 0.48
        d.ellipse([ax - r - lw, ay - r - lw, ax + r + lw, ay + r + lw], fill=outline)
        d.ellipse([ax - r, ay - r, ax + r, ay + r], fill=acc_color)
    elif acc == 'horns':
        for side in (-1, 1):
            hx = cx + side * bw * 0.55
            d.polygon([(hx - unit * 0.17, top_y + unit * 0.12), (hx + side * unit * 0.2, top_y - unit * 0.42), (hx + unit * 0.17, top_y + unit * 0.12)], fill=outline)
            d.polygon([(hx - unit * 0.12, top_y + unit * 0.1), (hx + side * unit * 0.17, top_y - unit * 0.34), (hx + unit * 0.12, top_y + unit * 0.1)], fill=acc_color)
    elif acc == 'cat_ears':
        for side in (-1, 1):
            hx = cx + side * bw * 0.55
            d.polygon([(hx - unit * 0.28, top_y + unit * 0.2), (hx + side * unit * 0.04, top_y - unit * 0.38), (hx + unit * 0.28, top_y + unit * 0.2)], fill=outline)
            d.polygon([(hx - unit * 0.2, top_y + unit * 0.16), (hx + side * unit * 0.04, top_y - unit * 0.26), (hx + unit * 0.2, top_y + unit * 0.16)], fill=hex_rgb(spec['cheeks']))
    elif acc == 'sprout':
        _line(d, (cx, top_y + lw), (cx, top_y - unit * 0.3), unit * 0.07, hex_rgb('#166534'))
        for side in (-1, 1):
            d.ellipse([cx + (0 if side > 0 else -unit * 0.34), top_y - unit * 0.5, cx + (unit * 0.34 if side > 0 else 0), top_y - unit * 0.18], fill=hex_rgb('#4ade80'), outline=hex_rgb('#166534'), width=lw)
    elif acc == 'halo':
        d.ellipse([cx - unit * 0.5, top_y - unit * 0.45, cx + unit * 0.5, top_y - unit * 0.2], outline=acc_color, width=int(unit * 0.09))

    # body
    mask = body_mask(spec['shape'], (N, N), cx, cy, bw, bh)
    grow = body_mask(spec['shape'], (N, N), cx, cy, bw + lw, bh + lw)
    if spec['shape'] == 'battery':
        d.rounded_rectangle([cx - bw * 0.3 - lw, cy - bh - unit * 0.2 - lw, cx + bw * 0.3 + lw, cy - bh + unit * 0.1], radius=unit * 0.06, fill=outline)
        d.rounded_rectangle([cx - bw * 0.3, cy - bh - unit * 0.2, cx + bw * 0.3, cy - bh + unit * 0.1], radius=unit * 0.05, fill=shade(body, 0.75))
        top_y -= unit * 0.2
    img.paste(Image.new('RGBA', (N, N), outline), (0, 0), grow)
    gradient = _vertical_gradient((N, N), shade(body, 1.22), shade(body, 0.82))
    img.paste(gradient, (0, 0), mask)
    shine = Image.new('L', (N, N), 0)
    ImageDraw.Draw(shine).ellipse([cx - bw * 0.78, cy - bh * 0.86, cx - bw * 0.1, cy - bh * 0.42], fill=110)
    img.paste(Image.new('RGBA', (N, N), (255, 255, 255, 255)), (0, 0), ImageChops.multiply(shine, mask))
    belly = Image.new('L', (N, N), 0)
    ImageDraw.Draw(belly).ellipse([cx - bw * 0.62, cy + bh * 0.02, cx + bw * 0.62, cy + bh * 1.05], fill=255)
    img.paste(Image.new('RGBA', (N, N), hex_rgb(spec['belly'], 255)), (0, 0), ImageChops.multiply(belly, mask).point(lambda v: v // 3))
    d = ImageDraw.Draw(img)
    if spec['shape'] == 'coin':
        d.ellipse([cx - bw * 0.82, cy - bh * 0.82, cx + bw * 0.82, cy + bh * 0.82], outline=shade(body, 0.7), width=int(lw * 1.2))
    # front arm tips drawn over the body edge look natural; ears/cap are on top of the head
    if acc == 'cap':
        d.pieslice([cx - bw * 0.9, top_y - bh * 0.18, cx + bw * 0.9, top_y + bh * 0.75], 180, 360, fill=acc_color, outline=outline, width=lw)
        d.rounded_rectangle([cx - bw * 0.1, top_y + bh * 0.2, cx + bw * 1.15, top_y + bh * 0.34], radius=unit * 0.07, fill=shade(acc_color, 0.85), outline=outline, width=lw)
    elif acc == 'crown':
        base = top_y + unit * 0.03
        pts = [(cx - bw * 0.55, base), (cx - bw * 0.62, base - unit * 0.42), (cx - bw * 0.28, base - unit * 0.2), (cx, base - unit * 0.5),
               (cx + bw * 0.28, base - unit * 0.2), (cx + bw * 0.62, base - unit * 0.42), (cx + bw * 0.55, base)]
        d.polygon(pts, fill=acc_color, outline=outline, width=lw)
    elif acc == 'headband':
        d.rounded_rectangle([cx - bw * 0.98, top_y + bh * 0.33, cx + bw * 0.98, top_y + bh * 0.5], radius=unit * 0.07, fill=hex_rgb('#ef4444'), outline=outline, width=lw)

    # face
    ex = bw * 0.42
    ey = cy - bh * 0.34
    look = pose.get('look', (0, 0))
    blink = max(float(pose.get('blink', 0)), lid_expr)
    style = spec['eyes']
    er = unit * (0.27 if style != 'big' else 0.34) * (1.22 if expr in ('shocked', 'scared') else 1.0)
    eye_color = hex_rgb(spec['eye_color'])
    if style == 'visor':
        vh = er * 0.9
        d.rounded_rectangle([cx - bw * 0.78, ey - vh, cx + bw * 0.78, ey + vh], radius=vh, fill=hex_rgb('#0f172a'), outline=outline, width=lw)
        for side in (-1, 1):
            gx = cx + side * ex + look[0] * er * 0.3
            h = vh * (1 - 0.85 * blink)
            d.rounded_rectangle([gx - er * 0.45, ey - h * 0.7, gx + er * 0.45, ey + h * 0.7], radius=er * 0.3, fill=accent)
    else:
        for side in (-1, 1):
            gx = cx + side * ex
            if style == 'dot':
                rx, ry = er * 0.5, er * 0.62 * (1 - 0.9 * blink)
                d.ellipse([gx - rx, ey - ry, gx + rx, ey + ry], fill=eye_color)
                d.ellipse([gx - rx * 0.4 + rx * 0.2, ey - ry * 0.55, gx + rx * 0.1 + rx * 0.2, ey - ry * 0.1], fill=(255, 255, 255, 255))
            else:
                rx, ry = er * 0.9, er * 1.0
                d.ellipse([gx - rx - lw, ey - ry - lw, gx + rx + lw, ey + ry + lw], fill=outline)
                d.ellipse([gx - rx, ey - ry, gx + rx, ey + ry], fill=(255, 255, 255, 255))
                pr = rx * (0.62 if expr not in ('shocked', 'scared') else 0.42)
                px = gx + look[0] * (rx - pr) * 0.85
                py = ey + look[1] * (ry - pr) * 0.85 + ry * 0.05
                d.ellipse([px - pr, py - pr, px + pr, py + pr], fill=eye_color)
                d.ellipse([px - pr * 0.55, py - pr * 0.65, px - pr * 0.05, py - pr * 0.15], fill=(255, 255, 255, 255))
                if blink > 0.02:
                    lid_h = (ry * 2 + lw * 2) * min(1.0, blink)
                    lid = Image.new('L', (N, N), 0)
                    ImageDraw.Draw(lid).rectangle([gx - rx - lw * 2, ey - ry - lw * 2, gx + rx + lw * 2, ey - ry - lw + lid_h], fill=255)
                    ellipse = Image.new('L', (N, N), 0)
                    ImageDraw.Draw(ellipse).ellipse([gx - rx - lw, ey - ry - lw, gx + rx + lw, ey + ry + lw], fill=255)
                    mix = ImageChops.multiply(lid, ellipse)
                    img.paste(Image.new('RGBA', (N, N), shade(body, 0.95)), (0, 0), mix)
                    d = ImageDraw.Draw(img)
                    d.line([gx - rx, ey - ry - lw + lid_h, gx + rx, ey - ry - lw + lid_h], fill=outline, width=lw)
    # brows
    if style != 'visor':
        for side in (-1, 1):
            gx = cx + side * ex
            by = ey - er * 1.25 - brow_raise * er * 0.9
            tilt = math.radians(brow_tilt) * side * -1
            if expr == 'thinking' and side == 1:tilt -= math.radians(14);by -= er * 0.3
            if expr == 'smug' and side == -1:tilt += math.radians(10)
            half = er * 0.8
            _line(d, (gx - half * math.cos(tilt), by - half * math.sin(tilt) * side * -1), (gx + half * math.cos(tilt), by + half * math.sin(tilt) * side * -1), unit * 0.085, outline)
    # cheeks
    for side in (-1, 1):
        cxk, cyk = cx + side * bw * 0.7, ey + er * 1.25
        cheek = Image.new('L', (N, N), 0)
        ImageDraw.Draw(cheek).ellipse([cxk - unit * 0.17, cyk - unit * 0.1, cxk + unit * 0.17, cyk + unit * 0.1], fill=120 if expr in ('happy', 'excited', 'proud', 'sad') else 70)
        img.paste(Image.new('RGBA', (N, N), hex_rgb(spec['cheeks'])), (0, 0), ImageChops.multiply(cheek, mask))
    d = ImageDraw.Draw(img)
    # mouth
    my = ey + er * 1.75
    mw = unit * 0.34
    open_amount = max(0.0, min(1.0, float(pose.get('mouth', 0))))
    dark = hex_rgb('#3b0d1f')
    if open_amount > 0.08 or mouth_kind in ('open', 'o'):
        h = unit * (0.07 + 0.3 * max(open_amount, 0.55 if mouth_kind == 'open' else 0.0, 0.5 if mouth_kind == 'o' else 0.0))
        w = mw * (0.55 if mouth_kind == 'o' else 1.0) * (0.75 + 0.35 * open_amount)
        d.ellipse([cx - w - lw, my - h * 0.35 - lw, cx + w + lw, my + h + lw], fill=outline)
        d.ellipse([cx - w, my - h * 0.35, cx + w, my + h], fill=dark)
        tongue = Image.new('L', (N, N), 0)
        ImageDraw.Draw(tongue).ellipse([cx - w * 0.7, my + h * 0.3, cx + w * 0.7, my + h * 1.3], fill=255)
        window = Image.new('L', (N, N), 0)
        ImageDraw.Draw(window).ellipse([cx - w, my - h * 0.35, cx + w, my + h], fill=255)
        img.paste(Image.new('RGBA', (N, N), hex_rgb('#fb7185')), (0, 0), ImageChops.multiply(tongue, window))
        d = ImageDraw.Draw(img)
    elif mouth_kind == 'grin':
        d.chord([cx - mw, my - unit * 0.22, cx + mw, my + unit * 0.2], 0, 180, fill=dark, outline=outline, width=lw)
    elif mouth_kind == 'frown':
        d.arc([cx - mw * 0.8, my, cx + mw * 0.8, my + unit * 0.3], 200, 340, fill=outline, width=int(lw * 1.4))
    elif mouth_kind == 'grit':
        d.rounded_rectangle([cx - mw * 0.8, my, cx + mw * 0.8, my + unit * 0.17], radius=unit * 0.05, fill=(255, 255, 255, 255), outline=outline, width=lw)
        for i in range(1, 5):
            xx = cx - mw * 0.8 + i * (mw * 1.6 / 5)
            d.line([xx, my, xx, my + unit * 0.17], fill=outline, width=max(1, lw // 2))
    elif mouth_kind == 'wavy':
        pts = [(cx - mw * 0.8 + i * mw * 1.6 / 8, my + unit * 0.05 * math.sin(i * 1.7)) for i in range(9)]
        d.line(pts, fill=outline, width=int(lw * 1.3), joint='curve')
    elif mouth_kind == 'wobble':
        pts = [(cx - mw * 0.7 + i * mw * 1.4 / 8, my + unit * 0.07 * (1 if i % 2 else -1)) for i in range(9)]
        d.line(pts, fill=outline, width=int(lw * 1.3), joint='curve')
    elif mouth_kind == 'side':
        d.line([cx - mw * 0.4, my + unit * 0.05, cx + mw * 0.8, my - unit * 0.03], fill=outline, width=int(lw * 1.4))
    elif mouth_kind == 'smirk':
        d.arc([cx - mw * 0.2, my - unit * 0.12, cx + mw * 0.9, my + unit * 0.12], 20, 170, fill=outline, width=int(lw * 1.4))
    else:
        d.arc([cx - mw * 0.7, my - unit * 0.12, cx + mw * 0.7, my + unit * 0.14], 20, 160, fill=outline, width=int(lw * 1.4))
    # glasses go over the eyes
    if acc == 'glasses':
        for side in (-1, 1):
            gx = cx + side * ex
            d.ellipse([gx - er * 1.1, ey - er * 1.1, gx + er * 1.1, ey + er * 1.1], outline=acc_color, width=int(lw * 1.5))
        d.line([cx - ex + er * 1.1, ey, cx + ex - er * 1.1, ey], fill=acc_color, width=int(lw * 1.5))
    # chest panel
    if spec['chest'] == 'xp':
        px0, px1 = cx - bw * 0.55, cx + bw * 0.55
        py0, py1 = cy + bh * 0.58, cy + bh * 0.86
        d.rounded_rectangle([px0 - lw, py0 - lw, px1 + lw, py1 + lw], radius=unit * 0.1, fill=outline)
        d.rounded_rectangle([px0, py0, px1, py1], radius=unit * 0.08, fill=hex_rgb('#0f172a'))
        fill = max(0.04, min(1.0, float(pose.get('xp', 0.6))))
        d.rounded_rectangle([px0 + lw, py0 + lw, px0 + lw + (px1 - px0 - 2 * lw) * fill, py1 - lw], radius=unit * 0.06, fill=accent)
        label = 'LV ' + str(pose.get('level', 1))
        f = font(int(unit * 0.15), 'Unbounded', 800)
        d.text(((px0 + px1) / 2, (py0 + py1) / 2), label, font=f, fill=(255, 255, 255, 255), stroke_width=max(1, lw // 2), stroke_fill=hex_rgb('#0f172a'), anchor='mm')
    elif spec['chest'] == 'heart':
        hx, hy, r = cx, cy + bh * 0.6, unit * 0.15
        d.ellipse([hx - r * 1.7, hy - r, hx, hy + r], fill=hex_rgb('#ef4444'));d.ellipse([hx, hy - r, hx + r * 1.7, hy + r], fill=hex_rgb('#ef4444'))
        d.polygon([(hx - r * 1.65, hy + r * 0.3), (hx + r * 1.65, hy + r * 0.3), (hx, hy + r * 2.2)], fill=hex_rgb('#ef4444'))
    elif spec['chest'] == 'bolt':
        hx, hy = cx, cy + bh * 0.66
        d.polygon([(hx + unit * 0.05, hy - unit * 0.3), (hx - unit * 0.18, hy + unit * 0.04), (hx - unit * 0.02, hy + unit * 0.04), (hx - unit * 0.07, hy + unit * 0.3), (hx + unit * 0.18, hy - unit * 0.06), (hx + unit * 0.02, hy - unit * 0.06)], fill=hex_rgb('#facc15'), outline=outline, width=lw)
    # expression extras
    fx = pose.get('fx', extra)
    ang = (t * 3)
    if fx == 'sparkle':
        _star(d, cx + bw * 1.05, cy - bh * 0.8 + math.sin(ang) * unit * 0.04, unit * 0.2, hex_rgb('#fde047'))
        _star(d, cx - bw * 1.1, cy - bh * 0.35, unit * 0.12, hex_rgb('#fde047'))
    elif fx == 'sweat':
        sx, sy = cx + bw * 0.9, cy - bh * 0.55
        d.polygon([(sx, sy - unit * 0.22), (sx - unit * 0.12, sy + unit * 0.05), (sx + unit * 0.12, sy + unit * 0.05)], fill=hex_rgb('#7dd3fc'))
        d.ellipse([sx - unit * 0.12, sy - unit * 0.07, sx + unit * 0.12, sy + unit * 0.17], fill=hex_rgb('#7dd3fc'))
    elif fx == 'tear':
        sx, sy = cx - bw * 0.42, ey + er * 1.2 + (t * 40 % 1) * unit * 0.3
        d.ellipse([sx - unit * 0.06, sy, sx + unit * 0.06, sy + unit * 0.16], fill=hex_rgb('#7dd3fc'))
    elif fx == 'anger':
        vx, vy, r = cx + bw * 0.7, cy - bh * 0.85, unit * 0.16
        red = hex_rgb('#ef4444')
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            d.line([(vx + sx * r * 0.35, vy + sy * r * 1.0), (vx + sx * r * 0.35, vy + sy * r * 0.35), (vx + sx * r * 1.0, vy + sy * r * 0.35)], fill=red, width=int(lw * 1.6), joint='curve')
    elif fx in ('zzz', 'question', 'exclaim'):
        f = font(int(unit * 0.5), 'Unbounded', 900)
        text = {'zzz': 'Z z', 'question': '?', 'exclaim': '!'}[fx]
        color = {'zzz': '#93c5fd', 'question': '#fde047', 'exclaim': '#fb7185'}[fx]
        d.text((cx + bw * 0.75, cy - bh * 1.2 - math.sin(ang) * unit * 0.05), text, font=f, fill=hex_rgb(color), stroke_width=lw, stroke_fill=outline)

    out = img.resize((size, size), Image.LANCZOS)
    tilt = float(pose.get('tilt', 0))
    if tilt:out = out.rotate(tilt, resample=Image.BICUBIC, center=(size / 2, size * 0.8))
    return out


def text_with_stroke(draw, xy, text, fnt, fill, stroke, width, anchor='la'):
    draw.text(xy, text, font=fnt, fill=fill, stroke_width=width, stroke_fill=stroke, anchor=anchor)
