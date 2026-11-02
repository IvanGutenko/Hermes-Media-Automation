"""The LVL cast: one fixed design per character, kept in a registry and reused in every episode.

Designs are specs for lvl_art (shape, colours, accessory), never regenerated per video. New guest characters can be
invented by the script writer; they are validated, made visually distinct from the existing cast, and saved for reuse."""
import json
import os
import time
from pathlib import Path
import lvl_art

BASE_DIR = Path(os.getenv('VIDEO_FACTORY_DIR', Path(__file__).resolve().parent.parent))
REGISTRY = BASE_DIR / 'data' / 'lvl' / 'characters.json'

SEED = {
    'lvl': {
        'name_ru': 'ЛВЛ', 'name_en': 'LVL', 'voice': 'hero', 'role': 'hero',
        'personality': 'Lazy, sarcastic, quick-witted student of life who keeps trying to level up and sometimes fails funnily.',
        'spec': lvl_art.default_spec(),
    },
    'len': {
        'name_ru': 'Лень', 'name_en': 'Sloth', 'voice': 'lazy', 'role': 'tempter',
        'personality': 'Heavy, sleepy, soft-spoken. Always suggests five more minutes. Never malicious, just comfortable.',
        'spec': lvl_art.clean_spec({'id': 'len', 'name': 'Sloth', 'shape': 'blob', 'body': '#94a3b8', 'accent': '#64748b', 'outline': '#1e293b',
            'belly': '#e2e8f0', 'eyes': 'round', 'accessory': 'cap', 'accessory_color': '#facc15', 'width': 1.12, 'height': 0.92}),
    },
    'dofamin': {
        'name_ru': 'Дофамин', 'name_en': 'Dopamine', 'voice': 'spark', 'role': 'tempter',
        'personality': 'Hyperactive and sparkly, chases every notification, offers instant rewards that cost you later.',
        'spec': lvl_art.clean_spec({'id': 'dofamin', 'name': 'Dopamine', 'shape': 'drop', 'body': '#f59e0b', 'accent': '#ef4444', 'outline': '#78350f',
            'belly': '#fde68a', 'eyes': 'big', 'accessory': 'antenna', 'accessory_color': '#ef4444', 'chest': 'bolt', 'width': 0.9, 'height': 1.05}),
    },
    'prokrastinaciya': {
        'name_ru': 'Прокрастинация', 'name_en': 'Procrastination', 'voice': 'sly', 'role': 'boss',
        'personality': 'Smooth and smug schemer. Says "tomorrow" in a very convincing way and keeps a perfect excuse list.',
        'spec': lvl_art.clean_spec({'id': 'prokrastinaciya', 'name': 'Procrastination', 'shape': 'cloud', 'body': '#38bdf8', 'accent': '#0ea5e9',
            'outline': '#0c4a6e', 'belly': '#e0f2fe', 'eyes': 'visor', 'accessory': 'horns', 'accessory_color': '#1e3a8a', 'chest': 'none', 'width': 1.15}),
    },
    'burnout': {
        'name_ru': 'Выгорание', 'name_en': 'Burnout', 'voice': 'boss', 'role': 'boss',
        'personality': 'Dim, slow, grey-orange battery that creeps in when you ignore sleep, food and rest.',
        'spec': lvl_art.clean_spec({'id': 'burnout', 'name': 'Burnout', 'shape': 'battery', 'body': '#f97316', 'accent': '#ef4444', 'outline': '#431407',
            'belly': '#fed7aa', 'eyes': 'dot', 'accessory': 'none', 'chest': 'none', 'height': 1.1}),
    },
    'kopilka': {
        'name_ru': 'Копилка', 'name_en': 'Piggy', 'voice': 'sly', 'role': 'ally',
        'personality': 'Calm money mentor with a coin slot, speaks in simple numbers, hates hidden fees.',
        'spec': lvl_art.clean_spec({'id': 'kopilka', 'name': 'Piggy', 'shape': 'coin', 'body': '#facc15', 'accent': '#16a34a', 'outline': '#713f12',
            'belly': '#fef9c3', 'eyes': 'round', 'accessory': 'glasses', 'accessory_color': '#1f2937', 'chest': 'heart'}),
    },
}
HERO = 'lvl'


def load():
    try:data = json.loads(REGISTRY.read_text())
    except (OSError, ValueError):data = {}
    merged = {k: dict(v) for k, v in SEED.items()}
    for k, v in data.items():
        if k in merged:merged[k]['appearances'] = v.get('appearances', 0)
        else:merged[k] = v
    return merged


def save(cast):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix('.tmp')
    tmp.write_text(json.dumps({k: {**v, 'spec': lvl_art.clean_spec(v['spec'])} for k, v in cast.items() if k not in SEED or v.get('appearances')},
        ensure_ascii=False, indent=1))
    tmp.replace(REGISTRY)


def color_distance(a, b):
    ra, rb = lvl_art.hex_rgb(a)[:3], lvl_art.hex_rgb(b)[:3]
    return sum((x - y) ** 2 for x, y in zip(ra, rb)) ** 0.5


def too_similar(spec, cast):
    """A new character must differ from every existing one by body colour or shape."""
    for other in cast.values():
        o = other['spec']
        if o['shape'] == spec['shape'] and color_distance(o['body'], spec['body']) < 90:return other['name_en']
    return None


def add_guest(proposal, cast=None):
    """Validate an invented character: {id,name_ru,name_en,voice,role,personality,spec{...}}. Returns the stored entry or raises ValueError."""
    cast = cast if cast is not None else load()
    spec = lvl_art.clean_spec({**(proposal.get('spec') or {}), 'id': proposal.get('id'), 'name': proposal.get('name_en')})
    name_ru, name_en = str(proposal.get('name_ru') or '').strip()[:28], str(proposal.get('name_en') or '').strip()[:28]
    if not name_ru or not name_en:raise ValueError('guest needs name_ru and name_en')
    if spec['id'] in cast:return cast[spec['id']]
    clash = too_similar(spec, cast)
    if clash:raise ValueError('looks too much like ' + clash)
    voice = proposal.get('voice') if proposal.get('voice') in ('hero', 'lazy', 'spark', 'sly', 'boss', 'narrator') else 'narrator'
    entry = {'name_ru': name_ru, 'name_en': name_en, 'voice': voice, 'role': str(proposal.get('role') or 'guest')[:20],
        'personality': str(proposal.get('personality') or '')[:240], 'spec': spec, 'created_at': time.time(), 'appearances': 0}
    cast[spec['id']] = entry
    return entry


def mark_used(cast, ids):
    for i in ids:
        if i in cast:cast[i]['appearances'] = cast[i].get('appearances', 0) + 1
    save(cast)


def describe(cast):
    """Compact text for the script writer's prompt."""
    return '\n'.join(f"- {k}: {v['name_en']} / {v['name_ru']} ({v['role']}, voice={v['voice']}): {v['personality']}" for k, v in cast.items())
