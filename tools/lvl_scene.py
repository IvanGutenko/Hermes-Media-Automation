"""Backgrounds, prop icons and RPG interface pieces for the LVL series (Pillow only)."""
import math
import random
from PIL import Image, ImageDraw
from lvl_art import font, hex_rgb, shade, _star

W, H = 1080, 1920
SETTINGS = ('room_night', 'room_day', 'city', 'gym', 'office', 'kitchen', 'park', 'brain', 'vault', 'void')
ICONS = ('battery', 'battery_low', 'coin', 'dumbbell', 'brain', 'moon', 'sun', 'phone', 'bed', 'apple', 'clock', 'chart_up', 'chart_down',
         'heart', 'bolt', 'lock', 'fire', 'coffee', 'book', 'shield', 'target', 'check', 'cross', 'trophy', 'bag', 'wallet', 'hourglass',
         'water', 'sleep', 'warning')
PALETTES = {
    'room_night': ('#1e1b4b', '#4c1d95'), 'room_day': ('#fde68a', '#fdba74'), 'city': ('#38bdf8', '#f9a8d4'), 'gym': ('#1f2937', '#7c3aed'),
    'office': ('#bae6fd', '#c7d2fe'), 'kitchen': ('#fef3c7', '#fecaca'), 'park': ('#7dd3fc', '#bef264'), 'brain': ('#0f172a', '#581c87'),
    'vault': ('#292524', '#78350f'), 'void': ('#0b1020', '#312e81'),
}


def gradient(size, top, bottom):
    w, h = size
    strip = Image.new('RGB', (1, h))
    t, b = hex_rgb(top), hex_rgb(bottom)
    for y in range(h):
        k = y / max(1, h - 1)
        strip.putpixel((0, y), tuple(int(t[i] + (b[i] - t[i]) * k) for i in range(3)))
    return strip.resize((w, h))


def _rr(d, box, r, fill, outline=None, width=0):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def background(setting, seed=0):
    """1080x1920 RGB background; deterministic for a (setting, seed) pair."""
    rnd = random.Random(f'{setting}:{seed}')
    top, bottom = PALETTES.get(setting, PALETTES['void'])
    img = gradient((W, H), top, bottom).convert('RGBA')
    d = ImageDraw.Draw(img)
    floor_y = int(H * 0.74)
    if setting in ('room_night', 'room_day', 'office', 'kitchen', 'gym'):
        dark = setting in ('room_night', 'gym')
        wall = shade(hex_rgb(top), 1.05 if not dark else 1.0)
        for x in range(0, W, 180):
            d.rectangle([x, 0, x + 90, floor_y], fill=shade(wall, 1.04 if not dark else 1.08))
        d.rectangle([0, floor_y, W, H], fill=shade(hex_rgb(bottom), 0.7 if dark else 0.92))
        d.rectangle([0, floor_y - 14, W, floor_y + 6], fill=shade(hex_rgb(bottom), 0.55 if dark else 0.75))
        for x in range(-200, W + 200, 220):
            d.polygon([(x, floor_y + 6), (x + 110, floor_y + 6), (x + 70, H), (x - 40, H)], fill=shade(hex_rgb(bottom), 0.66 if dark else 0.86))
        if setting in ('room_night', 'room_day', 'office'):
            _rr(d, [90, 330, 480, 740], 40, shade(hex_rgb('#0ea5e9' if setting != 'room_night' else '#1e1b4b'), 1.0), '#ffffff', 14)
            if setting == 'room_night':
                d.ellipse([290, 400, 390, 500], fill='#fef9c3')
                d.ellipse([320, 390, 410, 490], fill=shade(hex_rgb('#1e1b4b'), 1.0))
                for _ in range(10):
                    _star(d, rnd.randint(130, 450), rnd.randint(380, 700), rnd.randint(5, 10), '#fde68a')
            else:
                d.ellipse([160, 400, 260, 500], fill='#fde047')
                d.ellipse([260, 560, 420, 640], fill='#ffffff');d.ellipse([320, 530, 450, 640], fill='#ffffff')
            d.line([285, 330, 285, 740], fill='#ffffff', width=10)
            d.line([90, 535, 480, 535], fill='#ffffff', width=10)
            # desk with a monitor
            _rr(d, [560, floor_y - 120, 1060, floor_y - 84], 14, '#78350f')
            d.rectangle([590, floor_y - 84, 620, floor_y + 90], fill='#78350f');d.rectangle([1000, floor_y - 84, 1030, floor_y + 90], fill='#78350f')
            _rr(d, [660, floor_y - 440, 980, floor_y - 130], 20, '#0f172a', '#cbd5e1', 10)
            _rr(d, [680, floor_y - 420, 960, floor_y - 150], 12, '#0ea5e9' if setting != 'room_night' else '#6366f1')
            d.rectangle([800, floor_y - 130, 840, floor_y - 120], fill='#94a3b8')
        if setting == 'gym':
            for i, x in enumerate((140, 520, 880)):
                d.rectangle([x, 420, x + 24, floor_y], fill='#6b7280')
            d.rectangle([140, 800, 904, 826], fill='#9ca3af')
            for x in (180, 780):
                d.rounded_rectangle([x, 740, x + 50, 886], radius=12, fill='#f43f5e');d.rounded_rectangle([x + 54, 760, x + 84, 866], radius=10, fill='#be123c')
            for x in range(60, W, 240):
                d.line([x, 100, x + 120, 100], fill='#a78bfa', width=6)
        if setting == 'kitchen':
            for x in range(0, W, 120):
                for y in range(120, floor_y, 120):
                    d.rectangle([x, y, x + 116, y + 116], outline='#fecaca', width=3)
            _rr(d, [60, floor_y - 640, 380, floor_y], 24, '#e2e8f0', '#94a3b8', 8)
            _rr(d, [520, floor_y - 140, 1040, floor_y + 20], 16, '#a16207')
            d.ellipse([620, floor_y - 210, 700, floor_y - 140], fill='#ef4444');d.ellipse([720, floor_y - 205, 800, floor_y - 140], fill='#f59e0b')
        if setting == 'office':
            d.ellipse([740, 130, 900, 290], fill='#ffffff', outline='#475569', width=10)
            d.line([820, 210, 820, 160], fill='#0f172a', width=8);d.line([820, 210, 860, 230], fill='#0f172a', width=8)
    elif setting == 'city':
        for layer, (col, base, hmin, hmax) in enumerate((('#a78bfa', 0.62, 380, 760), ('#7c3aed', 0.68, 300, 640), ('#4c1d95', 0.74, 240, 540))):
            x = -40
            while x < W:
                bw = rnd.randint(120, 220);bh = rnd.randint(hmin, hmax)
                d.rectangle([x, int(H * base) - bh + 300, x + bw, H], fill=col)
                for wy in range(int(H * base) - bh + 330, int(H * base) + 250, 60):
                    for wx in range(x + 20, x + bw - 30, 50):
                        if rnd.random() < 0.45:d.rectangle([wx, wy, wx + 22, wy + 30], fill='#fde68a')
                x += bw + rnd.randint(6, 24)
        d.rectangle([0, floor_y + 100, W, H], fill='#1e1b4b')
        d.rectangle([0, floor_y + 90, W, floor_y + 110], fill='#475569')
        for x in range(0, W, 160):d.rectangle([x, floor_y + 330, x + 90, floor_y + 346], fill='#fde047')
    elif setting == 'park':
        d.ellipse([-200, floor_y - 260, 700, floor_y + 600], fill='#86efac');d.ellipse([400, floor_y - 180, 1300, floor_y + 600], fill='#4ade80')
        d.rectangle([0, floor_y + 40, W, H], fill='#22c55e')
        for x, s in ((120, 1.0), (880, 1.2), (560, 0.7)):
            d.rectangle([x - 20 * s, floor_y - 280 * s, x + 20 * s, floor_y + 20], fill='#92400e')
            d.ellipse([x - 130 * s, floor_y - 520 * s, x + 130 * s, floor_y - 200 * s], fill='#16a34a')
        d.ellipse([820, 200, 960, 340], fill='#fde047')
        d.ellipse([100, 300, 330, 380], fill='#ffffff');d.ellipse([180, 260, 360, 380], fill='#ffffff')
    elif setting == 'brain':
        nodes = [(rnd.randint(40, W - 40), rnd.randint(120, H - 120)) for _ in range(34)]
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:i + 4]:
                d.line([a, b], fill=(168, 85, 247, 120), width=4)
        for x, y in nodes:
            r = rnd.randint(8, 20)
            d.ellipse([x - r - 6, y - r - 6, x + r + 6, y + r + 6], fill=(168, 85, 247, 60));d.ellipse([x - r, y - r, x + r, y + r], fill='#e9d5ff')
    elif setting == 'vault':
        d.ellipse([W // 2 - 460, 260, W // 2 + 460, 1180], fill='#44403c', outline='#a8a29e', width=24)
        d.ellipse([W // 2 - 340, 380, W // 2 + 340, 1060], fill='#57534e', outline='#d6d3d1', width=12)
        for a in range(0, 360, 45):
            r = math.radians(a);d.line([W // 2, 720, W // 2 + 320 * math.cos(r), 720 + 320 * math.sin(r)], fill='#a8a29e', width=18)
        d.ellipse([W // 2 - 70, 650, W // 2 + 70, 790], fill='#fbbf24')
        for i in range(7):
            for j in range(rnd.randint(2, 6)):
                d.ellipse([60 + i * 150, floor_y + 60 - j * 26, 200 + i * 150, floor_y + 100 - j * 26], fill='#fbbf24', outline='#92400e', width=5)
        d.rectangle([0, floor_y + 100, W, H], fill='#1c1917')
    else:  # void: a retro neon grid
        horizon = int(H * 0.58)
        d.rectangle([0, horizon, W, H], fill='#0b1020')
        for i in range(-12, 13):
            d.line([W // 2 + i * 40, horizon, W // 2 + i * 190, H], fill='#6366f1', width=3)
        for k in range(1, 14):
            y = horizon + int((H - horizon) * (k / 13) ** 2)
            d.line([0, y, W, y], fill='#6366f1', width=3)
        for _ in range(60):
            d.ellipse([(x := rnd.randint(0, W)), (y := rnd.randint(0, horizon - 30)), x + 5, y + 5], fill='#e0e7ff')
        d.ellipse([W // 2 - 190, horizon - 380, W // 2 + 190, horizon], fill='#f472b6')
        d.rectangle([0, horizon - 4, W, horizon + 4], fill='#a78bfa')
    return img.convert('RGB')


def draw_icon(canvas, name, cx, cy, size, t=0.0, scale=1.0, alpha=255):
    """Flat vector prop drawn on `canvas` centred at (cx, cy); `size` is its width in pixels."""
    S = 4
    n = int(size * S)
    layer = Image.new('RGBA', (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    u = n / 200.0
    ol = max(3, int(7 * u))
    dark = hex_rgb('#1e1b4b')
    def box(x0, y0, x1, y1):return [x0 * u, y0 * u, x1 * u, y1 * u]
    if name in ('battery', 'battery_low'):
        d.rounded_rectangle(box(30, 55, 160, 150), radius=14 * u, fill='#f8fafc', outline=dark, width=ol)
        d.rounded_rectangle(box(160, 82, 178, 122), radius=6 * u, fill=dark)
        level = 0.13 if name == 'battery_low' else 0.85
        d.rounded_rectangle(box(40, 65, 40 + 110 * level, 140), radius=8 * u, fill='#ef4444' if name == 'battery_low' else '#22c55e')
    elif name == 'coin':
        d.ellipse(box(25, 25, 175, 175), fill='#fbbf24', outline=dark, width=ol);d.ellipse(box(48, 48, 152, 152), outline='#b45309', width=int(5 * u))
        d.text((100 * u, 102 * u), '$', font=font(int(80 * u), 'Unbounded', 900), fill='#b45309', anchor='mm')
    elif name == 'dumbbell':
        d.rounded_rectangle(box(60, 90, 140, 110), radius=6 * u, fill='#9ca3af', outline=dark, width=int(4 * u))
        for x0, x1 in ((22, 52), (148, 178)):d.rounded_rectangle(box(x0, 55, x1, 145), radius=10 * u, fill='#f43f5e', outline=dark, width=int(5 * u))
        for x0, x1 in ((50, 66), (134, 150)):d.rounded_rectangle(box(x0, 70, x1, 130), radius=6 * u, fill='#be123c', outline=dark, width=int(4 * u))
    elif name == 'brain':
        pink = hex_rgb('#f9a8d4')
        d.ellipse(box(22, 38, 108, 150), fill=pink, outline=dark, width=ol);d.ellipse(box(92, 38, 178, 150), fill=pink, outline=dark, width=ol)
        d.ellipse(box(30, 46, 104, 142), fill=pink);d.ellipse(box(96, 46, 170, 142), fill=pink)
        d.rounded_rectangle(box(60, 118, 140, 172), radius=22 * u, fill=hex_rgb('#f472b6'), outline=dark, width=int(5 * u))
        d.line([100 * u, 42 * u, 100 * u, 130 * u], fill=dark, width=int(5 * u))
        for pts in ([(44, 70), (62, 62), (78, 78), (92, 66)], [(40, 104), (58, 96), (74, 110), (90, 100)],
                    [(108, 66), (124, 78), (140, 62), (158, 72)], [(110, 100), (126, 112), (142, 96), (160, 106)]):
            d.line([(p[0] * u, p[1] * u) for p in pts], fill='#be185d', width=int(5 * u), joint='curve')
    elif name in ('moon', 'sleep'):
        d.ellipse(box(35, 30, 165, 160), fill='#fde68a', outline=dark, width=ol);d.ellipse(box(75, 15, 190, 140), fill='#00000000')
        d.ellipse(box(78, 20, 190, 135), fill=(0, 0, 0, 0))
        mask = Image.new('L', (n, n), 0);ImageDraw.Draw(mask).ellipse(box(35, 30, 165, 160), fill=255)
        cut = Image.new('L', (n, n), 0);ImageDraw.Draw(cut).ellipse(box(80, 18, 195, 135), fill=255)
        layer2 = Image.new('RGBA', (n, n), (0, 0, 0, 0))
        d2 = ImageDraw.Draw(layer2);d2.ellipse(box(35, 30, 165, 160), fill='#fde68a', outline=dark, width=ol)
        eraser = Image.new('L', (n, n), 255);ImageDraw.Draw(eraser).ellipse(box(80, 18, 195, 135), fill=0)
        layer2.putalpha(Image.composite(layer2.getchannel('A'), Image.new('L', (n, n), 0), eraser));layer = layer2;d = ImageDraw.Draw(layer)
        if name == 'sleep':d.text((150 * u, 55 * u), 'Z', font=font(int(60 * u), 'Unbounded', 900), fill='#93c5fd', anchor='mm', stroke_width=int(4 * u), stroke_fill=dark)
    elif name == 'sun':
        for a in range(0, 360, 30):
            r = math.radians(a);d.line([100 * u + 62 * u * math.cos(r), 100 * u + 62 * u * math.sin(r), 100 * u + 88 * u * math.cos(r), 100 * u + 88 * u * math.sin(r)], fill='#f59e0b', width=int(9 * u))
        d.ellipse(box(48, 48, 152, 152), fill='#fde047', outline=dark, width=ol)
    elif name == 'phone':
        d.rounded_rectangle(box(62, 18, 138, 182), radius=18 * u, fill='#0f172a', outline='#cbd5e1', width=ol)
        d.rounded_rectangle(box(70, 34, 130, 160), radius=8 * u, fill='#38bdf8')
        for k in range(3):d.rounded_rectangle(box(78, 46 + k * 34, 122, 68 + k * 34), radius=6 * u, fill='#f8fafc')
    elif name == 'bed':
        d.rounded_rectangle(box(20, 90, 180, 145), radius=10 * u, fill='#6366f1', outline=dark, width=ol)
        d.rounded_rectangle(box(28, 66, 80, 100), radius=10 * u, fill='#f8fafc', outline=dark, width=int(5 * u))
        d.rectangle(box(20, 140, 36, 170), fill=dark);d.rectangle(box(164, 140, 180, 170), fill=dark)
    elif name == 'apple':
        d.ellipse(box(40, 55, 106, 165), fill='#ef4444', outline=dark, width=ol);d.ellipse(box(94, 55, 160, 165), fill='#ef4444', outline=dark, width=ol)
        d.ellipse(box(48, 62, 152, 160), fill='#ef4444');d.line([100 * u, 55 * u, 108 * u, 22 * u], fill='#78350f', width=int(9 * u))
        d.ellipse(box(106, 22, 150, 52), fill='#22c55e', outline=dark, width=int(4 * u))
    elif name in ('clock', 'hourglass'):
        if name == 'clock':
            d.ellipse(box(28, 28, 172, 172), fill='#f8fafc', outline=dark, width=ol)
            a = t * 2
            d.line([100 * u, 100 * u, 100 * u, 52 * u], fill=dark, width=int(8 * u));d.line([100 * u, 100 * u, 100 * u + 40 * u * math.sin(a), 100 * u - 40 * u * math.cos(a)], fill='#ef4444', width=int(6 * u))
        else:
            d.polygon([(50 * u, 28 * u), (150 * u, 28 * u), (104 * u, 100 * u), (150 * u, 172 * u), (50 * u, 172 * u), (96 * u, 100 * u)], fill='#e0f2fe', outline=dark, width=ol)
            d.polygon([(70 * u, 150 * u), (130 * u, 150 * u), (100 * u, 118 * u)], fill='#fbbf24');d.rectangle(box(40, 20, 160, 32), fill=dark);d.rectangle(box(40, 168, 160, 180), fill=dark)
    elif name in ('chart_up', 'chart_down'):
        d.rounded_rectangle(box(22, 22, 178, 178), radius=18 * u, fill='#f8fafc', outline=dark, width=ol)
        up = name == 'chart_up'
        pts = [(46, 140), (84, 108), (112, 124), (152, 62)] if up else [(46, 62), (84, 94), (112, 78), (152, 142)]
        d.line([(p[0] * u, p[1] * u) for p in pts], fill='#22c55e' if up else '#ef4444', width=int(12 * u), joint='curve')
        ex, ey = pts[-1]
        d.polygon([(ex * u, (ey + (-20 if up else 20)) * u), ((ex - 22) * u, (ey + (6 if up else -6)) * u), ((ex + 14) * u, (ey + (14 if up else -14)) * u)], fill='#22c55e' if up else '#ef4444')
    elif name == 'heart':
        red = hex_rgb('#ef4444')
        d.polygon([(100 * u, 178 * u), (22 * u, 92 * u), (178 * u, 92 * u)], fill=dark)
        d.ellipse(box(18, 30, 108, 120), fill=dark);d.ellipse(box(92, 30, 182, 120), fill=dark)
        d.polygon([(100 * u, 166 * u), (32 * u, 94 * u), (168 * u, 94 * u)], fill=red)
        d.ellipse(box(28, 38, 102, 112), fill=red);d.ellipse(box(98, 38, 172, 112), fill=red)
        d.ellipse(box(44, 52, 66, 72), fill='#fecaca')
    elif name == 'bolt':
        d.polygon([(112 * u, 14 * u), (46 * u, 108 * u), (92 * u, 108 * u), (78 * u, 188 * u), (156 * u, 80 * u), (108 * u, 80 * u)], fill='#fde047', outline=dark, width=ol)
    elif name == 'lock':
        d.arc(box(54, 20, 146, 120), 180, 360, fill='#94a3b8', width=int(16 * u));d.rounded_rectangle(box(38, 84, 162, 172), radius=16 * u, fill='#fbbf24', outline=dark, width=ol)
        d.ellipse(box(88, 110, 112, 134), fill=dark);d.rectangle(box(96, 126, 104, 154), fill=dark)
    elif name == 'fire':
        d.polygon([(100 * u, 10 * u), (150 * u, 80 * u), (162 * u, 124 * u), (130 * u, 176 * u), (70 * u, 176 * u), (38 * u, 124 * u), (56 * u, 74 * u), (82 * u, 100 * u)], fill='#f97316', outline=dark, width=ol)
        d.polygon([(100 * u, 80 * u), (130 * u, 130 * u), (118 * u, 168 * u), (82 * u, 168 * u), (72 * u, 130 * u)], fill='#fde047')
    elif name == 'coffee':
        d.rounded_rectangle(box(36, 70, 140, 160), radius=16 * u, fill='#f8fafc', outline=dark, width=ol)
        d.arc(box(120, 84, 174, 140), 270, 90, fill=dark, width=int(10 * u));d.rectangle(box(48, 82, 128, 98), fill='#78350f')
        for x in (66, 92, 118):d.arc(box(x - 8, 20, x + 8, 64), 90, 270, fill='#cbd5e1', width=int(6 * u))
    elif name == 'book':
        d.polygon([(22 * u, 50 * u), (100 * u, 70 * u), (100 * u, 170 * u), (22 * u, 150 * u)], fill='#60a5fa', outline=dark)
        d.polygon([(178 * u, 50 * u), (100 * u, 70 * u), (100 * u, 170 * u), (178 * u, 150 * u)], fill='#3b82f6', outline=dark)
        d.line([100 * u, 70 * u, 100 * u, 170 * u], fill=dark, width=int(6 * u))
        for k in range(3):d.line([36 * u, (74 + k * 22) * u, 88 * u, (86 + k * 22) * u], fill='#dbeafe', width=int(5 * u))
    elif name == 'shield':
        d.polygon([(100 * u, 14 * u), (172 * u, 40 * u), (166 * u, 112 * u), (100 * u, 184 * u), (34 * u, 112 * u), (28 * u, 40 * u)], fill='#3b82f6', outline=dark, width=ol)
        d.polygon([(100 * u, 38 * u), (148 * u, 56 * u), (144 * u, 106 * u), (100 * u, 154 * u)], fill='#93c5fd')
    elif name == 'target':
        for r, col in ((82, '#ef4444'), (62, '#f8fafc'), (44, '#ef4444'), (24, '#f8fafc'), (10, '#ef4444')):
            d.ellipse(box(100 - r, 100 - r, 100 + r, 100 + r), fill=col, outline=dark, width=int(3 * u))
    elif name == 'check':
        d.ellipse(box(20, 20, 180, 180), fill='#22c55e', outline=dark, width=ol)
        d.line([(58 * u, 104 * u), (90 * u, 136 * u), (146 * u, 68 * u)], fill='#f8fafc', width=int(18 * u), joint='curve')
    elif name == 'cross':
        d.ellipse(box(20, 20, 180, 180), fill='#ef4444', outline=dark, width=ol)
        d.line([60 * u, 60 * u, 140 * u, 140 * u], fill='#f8fafc', width=int(18 * u));d.line([140 * u, 60 * u, 60 * u, 140 * u], fill='#f8fafc', width=int(18 * u))
    elif name == 'trophy':
        d.polygon([(54 * u, 30 * u), (146 * u, 30 * u), (136 * u, 100 * u), (100 * u, 124 * u), (64 * u, 100 * u)], fill='#fbbf24', outline=dark, width=ol)
        d.arc(box(18, 36, 66, 90), 90, 270, fill=dark, width=int(8 * u));d.arc(box(134, 36, 182, 90), 270, 90, fill=dark, width=int(8 * u))
        d.rectangle(box(90, 120, 110, 150), fill='#f59e0b');d.rounded_rectangle(box(60, 148, 140, 174), radius=8 * u, fill='#b45309', outline=dark, width=int(4 * u))
    elif name == 'bag':
        d.rounded_rectangle(box(40, 70, 160, 176), radius=14 * u, fill='#f472b6', outline=dark, width=ol);d.arc(box(66, 28, 134, 106), 180, 360, fill=dark, width=int(10 * u))
    elif name == 'wallet':
        d.rounded_rectangle(box(22, 52, 178, 158), radius=18 * u, fill='#a16207', outline=dark, width=ol);d.rounded_rectangle(box(120, 90, 182, 128), radius=12 * u, fill='#fbbf24', outline=dark, width=int(5 * u))
        d.ellipse(box(140, 102, 156, 118), fill=dark)
    elif name == 'water':
        d.polygon([(100 * u, 16 * u), (150 * u, 100 * u), (152 * u, 130 * u), (100 * u, 182 * u), (48 * u, 130 * u), (50 * u, 100 * u)], fill='#38bdf8', outline=dark, width=ol)
        d.ellipse(box(70, 100, 92, 140), fill='#bae6fd')
    elif name == 'warning':
        d.polygon([(100 * u, 20 * u), (180 * u, 168 * u), (20 * u, 168 * u)], fill='#fbbf24', outline=dark, width=ol)
        d.rectangle(box(92, 70, 108, 124), fill=dark);d.ellipse(box(90, 134, 110, 154), fill=dark)
    else:
        d.ellipse(box(30, 30, 170, 170), fill='#a78bfa', outline=dark, width=ol)
        d.text((100 * u, 102 * u), '?', font=font(int(90 * u), 'Unbounded', 900), fill='#f8fafc', anchor='mm')
    sprite = layer.resize((int(size * scale), int(size * scale)), Image.LANCZOS) if scale != 1.0 else layer.resize((int(size), int(size)), Image.LANCZOS)
    if alpha < 255:
        a = sprite.getchannel('A').point(lambda v: v * alpha // 255);sprite.putalpha(a)
    canvas.alpha_composite(sprite, (int(cx - sprite.width / 2), int(cy - sprite.height / 2))) if canvas.mode == 'RGBA' else canvas.paste(sprite, (int(cx - sprite.width / 2), int(cy - sprite.height / 2)), sprite)


def pop_scale(k):
    """Overshoot easing for a sticker appearing: k in 0..1 -> scale 0..~1.1..1."""
    if k <= 0:return 0.0
    if k >= 1:return 1.0
    return 1 + 2.7 * (k - 1) ** 3 + 1.7 * (k - 1) ** 2


def hud(canvas, xp, level, name='LVL', title=None):
    """Top RPG strip: level badge, XP bar and the series name."""
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([40, 56, 1040, 150], radius=36, fill=(15, 23, 42, 215), outline=(167, 139, 250, 255), width=5)
    d.ellipse([52, 62, 142, 148], fill='#8b5cf6', outline='#ede9fe', width=5)
    d.text((97, 106), str(level), font=font(46, 'Unbounded', 900), fill='#ffffff', anchor='mm')
    d.text((165, 80), name, font=font(30, 'Unbounded', 800), fill='#e9d5ff', anchor='lm')
    d.rounded_rectangle([165, 100, 1010, 132], radius=16, fill='#1e293b')
    fill = max(0.02, min(1.0, xp))
    d.rounded_rectangle([168, 103, 168 + (839 - 4) * fill, 129], radius=13, fill='#22c55e')
    d.text((1000, 80), 'XP', font=font(28, 'Unbounded', 800), fill='#86efac', anchor='rm')


def xp_popup(canvas, text, cx, cy, k, color='#86efac'):
    """Floating '+10 XP' that rises and fades; k in 0..1."""
    if k <= 0 or k >= 1:return
    layer = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    alpha = int(255 * min(1, (1 - k) * 2.2))
    d.text((cx, cy - 160 * k), text, font=font(84, 'Unbounded', 900), fill=hex_rgb(color, alpha), anchor='mm', stroke_width=7, stroke_fill=(15, 23, 42, alpha))
    canvas.alpha_composite(layer)


def level_up(canvas, k, level):
    """Burst of rays and a banner; k in 0..1."""
    if k <= 0 or k >= 1:return
    layer = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = canvas.width // 2, int(canvas.height * 0.27)
    alpha = int(255 * min(1, (1 - k) * 2.5, k * 6))
    for a in range(0, 360, 20):
        r = math.radians(a + k * 40)
        d.polygon([(cx, cy), (cx + 900 * math.cos(r - 0.07), cy + 900 * math.sin(r - 0.07)), (cx + 900 * math.cos(r + 0.07), cy + 900 * math.sin(r + 0.07))], fill=(253, 224, 71, alpha // 3))
    s = pop_scale(min(1, k * 4))
    d.rounded_rectangle([cx - 420 * s, cy - 110 * s, cx + 420 * s, cy + 110 * s], radius=50, fill=(124, 58, 237, alpha), outline=(253, 224, 71, alpha), width=10)
    d.text((cx, cy - 20 * s), 'LEVEL UP!', font=font(int(96 * s) + 1, 'Unbounded', 900), fill=(255, 255, 255, alpha), anchor='mm', stroke_width=6, stroke_fill=(30, 27, 75, alpha))
    d.text((cx, cy + 62 * s), f'LV {level}', font=font(int(46 * s) + 1, 'Unbounded', 800), fill=(253, 224, 71, alpha), anchor='mm')
    canvas.alpha_composite(layer)


def chunk_words(words, max_words=4, max_chars=26):
    """Split a spoken line into short caption phrases: [(start_index, [words])]."""
    chunks, cur, start = [], [], 0
    for i, w in enumerate(words):
        if cur and (len(cur) >= max_words or len(' '.join(cur + [w])) > max_chars or cur[-1].endswith(('.', '!', '?', ',', ':', ';'))):
            chunks.append((start, cur));cur, start = [], i
        cur.append(w)
    if cur:chunks.append((start, cur))
    return chunks


def caption(canvas, words, active, y=1480, width=960, size=84):
    """Karaoke caption for one short phrase: the spoken word is highlighted. `words` is the phrase only."""
    if not words:return
    d = ImageDraw.Draw(canvas)
    fnt = font(size, 'Rubik', 900)
    lines, line = [], []
    for w in words:
        trial = ' '.join(line + [w])
        if d.textlength(trial, font=fnt) > width and line:
            lines.append(line);line = [w]
        else:line.append(w)
    lines.append(line)
    base = 0
    for li, parts in enumerate(lines):
        text = ' '.join(parts)
        gap = 14
        total = d.textlength(text, font=fnt) + gap * (len(parts) - 1)
        x = (canvas.width - total) / 2
        yy = y + li * (size + 16)
        for wi, w in enumerate(parts):
            color = '#fde047' if base + wi == active else '#ffffff'
            d.text((x, yy), w, font=fnt, fill=color, stroke_width=10, stroke_fill='#1e1b4b')
            x += d.textlength(w + ' ', font=fnt) + gap
        base += len(parts)
