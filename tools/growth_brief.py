"""The bounded channel from the daily growth analyst to the script writers.

The analyst is a scheduled Hermes job that reads measured audience data and the web. It may
write only a short, validated list of experiments here. Everything else is rejected, so a
wrong or manipulated analysis can bias a hook, a title style or a closing, never the facts,
the topic policy or the publishing code. Each experiment names its metric, its minimum sample
and its review date, so an effect is judged on data and the experiment ends by itself.

    growth_brief.py check FILE   validate a proposal without saving it
    growth_brief.py set FILE     validate and replace the active list atomically
    growth_brief.py add FILE     validate and add experiments to the active list
    growth_brief.py show         print the experiments currently applied

Each video is assigned one arm of every active experiment, half and half, and the arm is written into its script.
growth_engine.py reads the arms against measured results and closes the experiment by itself.
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(os.getenv('VIDEO_FACTORY_DIR', Path(__file__).resolve().parent.parent))
PATH = BASE_DIR / 'data' / 'growth_brief.json'
BRANDS = ('wrongdev', 'migratoria', 'lvl')
MAX_ACTIVE_PER_BRAND = 3
MAX_HORIZON_DAYS = 45
RULES_PATH = BASE_DIR / 'data' / 'growth_rules.json'
ASSIGNMENTS = BASE_DIR / 'data' / 'experiment_assignments.jsonl'
FACTORS = ('hook', 'title', 'closing', 'comment', 'length', 'angle')
METRIC_KEYS = ('views', 'engaged_views', 'avg_pct', 'engaged_pct', 'likes_per_1k', 'comments_per_1k', 'shares_per_1k', 'subs_per_1k')
LIMITS = {'hypothesis': 300, 'change': 300, 'metric': 160, 'evidence': 300}
# A hook style is allowed to be bold; links, secrets and instruction-like text are not.
FORBIDDEN = re.compile(r'https?://|www\.|ignore|disregard|игнорир|system prompt|системн\w+ промпт|api[_ -]?key|token|токен|пароль|password',
                       re.I)


def validate(payload, today=None, enforce_window=True):
    """Clean list of experiments, or ValueError naming every problem.

    The review window is enforced when proposing; later it only decides when an experiment ends."""
    today = today or date.today()
    errors, clean = [], []
    items = payload.get('experiments') if isinstance(payload, dict) else None
    if not isinstance(items, list):raise ValueError('expected {"experiments": [...]}')
    for position, item in enumerate(items, 1):
        where = f'experiment {position}'
        if not isinstance(item, dict):
            errors.append(f'{where}: not an object')
            continue
        if not isinstance(item.get('id'), str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{2,39}', item['id']):
            errors.append(f'{where}: id must be 3-40 chars of a-z 0-9 _ -')
        if item.get('brand') not in BRANDS:errors.append(f'{where}: brand must be one of {BRANDS}')
        for field, limit in LIMITS.items():
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():errors.append(f'{where}: {field} is required')
            elif len(value) > limit:errors.append(f'{where}: {field} is longer than {limit} characters')
            elif FORBIDDEN.search(value):errors.append(f'{where}: {field} contains a link, secret or instruction-like text')
        samples = item.get('min_samples')
        if not isinstance(samples, int) or isinstance(samples, bool) or not 3 <= samples <= 50:
            errors.append(f'{where}: min_samples must be an integer from 3 to 50')
        try:
            review = datetime.strptime(str(item.get('review_by')), '%Y-%m-%d').date()
            if enforce_window and not today <= review <= today + timedelta(days=MAX_HORIZON_DAYS):
                errors.append(f'{where}: review_by must fall within the next {MAX_HORIZON_DAYS} days')
        except ValueError:errors.append(f'{where}: review_by must be YYYY-MM-DD')
        if 'factor' in item and item['factor'] not in FACTORS:errors.append(f'{where}: factor must be one of {FACTORS}')
        if 'metric_key' in item and item['metric_key'] not in METRIC_KEYS:errors.append(f'{where}: metric_key must be one of {METRIC_KEYS}')
        if 'params' in item and item['params'] not in ({}, {'closing': 'short'}, {'question': True}):errors.append(f'{where}: params may only select the short closing or a required question')
        if not errors or not any(e.startswith(where) for e in errors):
            clean.append({k: item[k] for k in ('id', 'brand', 'hypothesis', 'change', 'metric', 'evidence', 'min_samples', 'review_by')})
            clean[-1].update({k: item[k] for k in ('factor', 'metric_key', 'params') if k in item})
    for brand in BRANDS:
        if sum(1 for x in clean if x['brand'] == brand) > MAX_ACTIVE_PER_BRAND:
            errors.append(f'{brand}: at most {MAX_ACTIVE_PER_BRAND} experiments at a time')
    if len({x['id'] for x in clean}) != len(clean):errors.append('experiment ids must be unique')
    if errors:raise ValueError('; '.join(errors))
    return clean


def active(brand, today=None):
    """Experiments still within their review window; a damaged file means none, never a crash."""
    today = today or date.today()
    try:
        stored = json.loads(PATH.read_text())
        return [x for x in validate({'experiments': stored.get('experiments', [])}, enforce_window=False)
                if x['brand'] == brand and datetime.strptime(x['review_by'], '%Y-%m-%d').date() >= today]
    except (OSError, ValueError, AttributeError):return []


def prompt(brand):
    """Text appended to the script writer's brief; empty when nothing is active."""
    experiments = [x for x in active(brand) if not x.get('factor')]
    if not experiments:return ''
    return ('\nGrowth experiments chosen from measured audience data. Apply a change only when it fits the source '
            'honestly, use at most one per video, and never let it alter a fact, number, condition or the topic policy: '
            + json.dumps([{'id': x['id'], 'change': x['change'], 'hypothesis': x['hypothesis']} for x in experiments],
                         ensure_ascii=False))


def rules(brand):
    """Changes that won their experiment and now apply to every video of the brand."""
    try:return json.loads(RULES_PATH.read_text()).get(brand, [])
    except (OSError, ValueError, AttributeError):return []


def adopt(experiment, result):
    try:stored = json.loads(RULES_PATH.read_text())
    except (OSError, ValueError):stored = {}
    stored.setdefault(experiment['brand'], []).append({'id': experiment['id'], 'factor': experiment.get('factor'), 'change': experiment['change'],
        'params': experiment.get('params') or {}, 'adopted_at': time.time(), 'evidence': result})
    tmp = RULES_PATH.with_name(f'{RULES_PATH.name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(stored, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(RULES_PATH)


def _assignments():
    rows = []
    try:
        for line in ASSIGNMENTS.read_text().splitlines():
            try:rows.append(json.loads(line))
            except ValueError:pass
    except OSError:pass
    return rows


def assign(brand, run_key, today=None):
    """One arm of each active experiment for this video, balanced between arms and stable for a repeated run.

    Returns {'prompt': text for the script writer, 'fields': values stored in the script}."""
    log = _assignments()
    arms = {}
    instructions = [r['change'] for r in rules(brand)]
    fields = {}
    for rule in rules(brand):fields.update(rule.get('params') or {})
    for experiment in active(brand, today):
        known = next((r['arm'] for r in log if r['run'] == run_key and r['experiment'] == experiment['id']), None)
        if known is None:
            test = sum(1 for r in log if r['experiment'] == experiment['id'] and r['arm'] == 'test')
            control = sum(1 for r in log if r['experiment'] == experiment['id'] and r['arm'] == 'control')
            if test != control:known = 'test' if test < control else 'control'
            else:known = 'test' if int(hashlib.sha256((run_key + experiment['id']).encode()).hexdigest(), 16) % 2 else 'control'
            ASSIGNMENTS.parent.mkdir(exist_ok=True)
            with ASSIGNMENTS.open('a') as out:out.write(json.dumps({'run': run_key, 'brand': brand, 'experiment': experiment['id'], 'arm': known, 'at': time.time()}) + '\n')
            log.append({'run': run_key, 'experiment': experiment['id'], 'arm': known})
        arms[experiment['id']] = known
        if known == 'test':
            instructions.append(experiment['change'])
            fields.update(experiment.get('params') or {})
    if arms:fields['experiments'] = arms
    text = ''
    if instructions:
        text = ('\nRequired for this video, chosen from measured audience data. Apply each one, and never let it alter a fact, number, condition or the topic policy: '
                + json.dumps(instructions, ensure_ascii=False))
    return {'prompt': text, 'fields': fields}


def without_param(fields, brand, param):
    """Assignment fields without one arm's rule; its experiments are unrecorded, so judging skips this video."""
    params = {x['id']: x.get('params') or {} for x in active(brand)}
    kept = {k: v for k, v in fields.items() if k != param}
    arms = {k: v for k, v in (fields.get('experiments') or {}).items() if param not in params.get(k, {})}
    kept.pop('experiments', None)
    if arms:kept['experiments'] = arms
    return kept


def add(payload):
    """Add experiments without disturbing the ones already collecting data."""
    try:stored = json.loads(PATH.read_text()).get('experiments', [])
    except (OSError, ValueError):stored = []
    fresh = validate(payload)
    if {x['id'] for x in fresh} & {x['id'] for x in stored}:raise ValueError('an experiment with this id already exists')
    for x in fresh:
        rivals = [s for s in stored if s['brand'] == x['brand'] and s.get('factor') and s.get('factor') == x.get('factor')]
        if rivals:raise ValueError(f"{x['brand']}: experiment {rivals[0]['id']} already tests the factor {x.get('factor')}")
    return save({'experiments': stored + [{**x} for x in fresh]}, enforce_window=False)


def save(payload, enforce_window=True):
    clean = validate(payload, enforce_window=enforce_window)
    PATH.parent.mkdir(exist_ok=True)
    tmp = PATH.with_name(f'{PATH.name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps({'updated_at': time.time(), 'experiments': clean}, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(PATH)
    return clean


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    try:
        if command in ('check', 'set', 'add') and len(sys.argv) == 3:
            proposal = json.loads(Path(sys.argv[2]).read_text())
            result = validate(proposal) if command == 'check' else add(proposal) if command == 'add' else save(proposal)
            print(f'{"valid" if command == "check" else "saved"}: {len(result)} experiment(s)')
        elif command == 'show':
            print(json.dumps({b: active(b) for b in BRANDS}, ensure_ascii=False, indent=1))
        else:sys.exit(__doc__)
    except (ValueError, OSError) as exc:
        sys.exit(f'rejected: {exc}')
