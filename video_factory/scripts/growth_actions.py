"""Measured editorial actions consumed by the live factory, not just a daily report.

No new accounts, sources, facts or publishing settings. Topic weights use one
24-30 hour observation per URL, separately by platform, format and language.
They are exploratory allocations, not causal findings. One fifth of choices
remains uniform exploration. Script actions never overlap a running experiment.
"""
import hashlib
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

BASE_DIR = Path(os.getenv('VIDEO_FACTORY_DIR', Path(__file__).resolve().parent.parent))
DATA = BASE_DIR / 'data'
PATH = DATA / 'growth_actions.json'
LOG = DATA / 'growth_consumption.jsonl'
MAX_AGE = 86400
EXPLORATION = 0.2
RECIPES = {
    'early_payoff': (('angle', 'hook'), 'Show the surprising result or the failed attempt in the first two scenes, then explain it. Use only the supplied evidence; do not delay the answer until the ending. Keep the story and character acting specific to this episode.'),
    'concrete_opening': (('hook',), 'Start with a visible problem and its concrete consequence for the viewer. Avoid introductions and abstract promises. The first spoken sentence and first action must concern the same problem.'),
}


def load(path, default=None):
    try:return json.loads(Path(path).read_text())
    except (OSError, ValueError):return {} if default is None else default


def classify(brand, item):
    if brand == 'lvl':
        run = str(item.get('source_episode') or item.get('run_id') or '')
        if not run or Path(run).name != run:return 'other'
        if item.get('kind')=='post' and not item.get('source_episode'):
            run=load(BASE_DIR/'output/lvl_posts'/run/'post.json').get('source_episode',run)
            if Path(run).name!=run:return 'other'
        saved = load(BASE_DIR / 'output/lvl_runs' / run / 'script.json')
        return saved.get('topic', {}).get('rubric', 'other')
    from growth_engine import topic_class
    return topic_class(brand, (item.get('title') or '') + ' ' + (item.get('text') or item.get('original_text') or '')[:300])


def refresh(now=None, notify=False):
    now = time.time() if now is None else now
    previous=load(PATH)
    report = load(DATA / 'growth_report.json')
    if not 0 <= now - report.get('updated_at', 0) <= MAX_AGE:
        return {'status': 'waiting_for_fresh_measurements'}
    cohorts = load(DATA / 'content_age_cohorts.json')
    import growth_brief
    plans = {}
    for brand in ('wrongdev', 'migratoria', 'lvl'):
        cells, seen = {}, set()
        for item in cohorts.values():
            metrics = item.get('metrics') or {}
            published = item.get('published_at')
            key = (item.get('platform'), item.get('kind'), item.get('lang') or '', item.get('url'))
            if (item.get('brand') != brand or key in seen or not 24 <= (item.get('age_hours') or 0) <= 30
                    or metrics.get('views') is None or metrics.get('stale') or not published or now-published > 30*86400):continue
            seen.add(key)
            cell = '|'.join(key[:3])
            cells.setdefault(cell, {}).setdefault(classify(brand, item), []).append(metrics['views'])
        allocations = {}
        for cell, topics in cells.items():
            overall = statistics.median(v for values in topics.values() for v in values)
            groups = {}
            for topic, values in topics.items():
                n, med = len(values), statistics.median(values)
                # Small cells raise a modest hypothesis; a single hit never changes allocation.
                weight = 1.0 if n < 2 else round(max(.75, min(1.35, ((med+1)/(overall+1)) ** (n/(n+4)))), 3)
                groups[topic] = {'n': n, 'median_views': med, 'weight': weight,
                    'status': 'observational' if n >= 4 else 'hypothesis' if n >= 2 else 'single_no_bias'}
            allocations[cell] = {'age_hours': [24, 30], 'median_views': overall, 'topics': groups}
        info = report.get('brands', {}).get(brand, {})
        mature = [v for v in info.get('videos', []) if (v.get('age_hours') or 0) >= 72]
        busy = {x.get('factor') for x in growth_brief.active(brand)}
        actions = []
        for action, stage, field in (('early_payoff', 'viewers_leave_early', 'avg_pct'), ('concrete_opening', 'weak_opening', 'engaged_pct')):
            affected = [v for v in mature if any(d.get('stage') == stage for d in v.get('diagnosis', []))]
            if brand == 'lvl' and action == 'early_payoff':
                # A whole small series can have weak retention, so comparing only
                # with its own low median hides the issue. Hypothesis, not a win.
                affected = [v for v in mature if v.get('avg_pct') is not None and v['avg_pct'] < 60]
                languages = {}
                for video in affected:languages.setdefault(video.get('lang') or 'unknown', []).append(video)
                affected = max(languages.values(), key=len, default=[])
            if len(affected) >= 2 and busy.isdisjoint(RECIPES[action][0]):
                actions.append({'id': action, 'status': 'hypothesis', 'n': len(affected),
                    'metric': field, 'median': statistics.median(v[field] for v in affected if v.get(field) is not None),
                    'lang': affected[0].get('lang'),
                    'urls': [v['url'] for v in affected]})
        plans[brand] = {'allocations': allocations, 'actions': actions, 'protected_experiment_factors': sorted(x for x in busy if x),
            'note': 'Same-age observations guide reversible allocation; they do not establish a cause. Sparse topics remain in exploration.'}
    policy = {'brands': plans, 'exploration_fraction': EXPLORATION}
    revision = hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    state = {'status': 'active', 'revision': revision, 'updated_at': now, 'measurements_at': report['updated_at'], **policy}
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_name(f'{PATH.name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1));tmp.chmod(0o600);tmp.replace(PATH)
    if notify:notify_decisions(previous,state)
    return state


def policy_decisions(plan):
    """Stable editorial choices; changing a counter alone is not a new decision."""
    choices={}
    for action in plan.get('actions',[]):
        choices['action|'+action['id']+'|'+(action.get('lang') or '')]='apply'
    for cell,allocation in plan.get('allocations',{}).items():
        for topic,info in allocation.get('topics',{}).items():
            if info.get('n',0)<4:continue
            weight=info.get('weight',1)
            if weight>=1.1:choices['topic|'+cell+'|'+topic]='increase'
            elif weight<=.9:choices['topic|'+cell+'|'+topic]='decrease'
    return choices


def notify_decisions(previous,current):
    if previous.get('status')!='active':return
    import html
    import owner_notify
    topics={'incident':'инциденты и безопасность','pricing_limits':'цены и ограничения',
        'release':'релизы','howto':'практические разборы','visa_border':'визы и въезд',
        'loyalty':'программы лояльности','premium_curiosity':'необычные особенности поездок',
        'route':'маршруты','life_abroad':'жизнь за рубежом','money':'деньги','body':'движение',
        'brain':'внимание и обучение','energy':'энергия','habits':'привычки','other':'другие темы'}
    actions={'early_payoff':'показывать результат раньше','concrete_opening':'начинать с конкретной проблемы'}
    for brand,plan in current.get('brands',{}).items():
        old=policy_decisions(previous.get('brands',{}).get(brand,{}));new=policy_decisions(plan)
        changes={k:new.get(k,'normal') for k in old.keys()|new.keys() if old.get(k)!=new.get(k)}
        if not changes:continue
        lines=[]
        for key,value in sorted(changes.items()):
            fields=key.split('|')
            if fields[0]=='action':
                verb='Применяю' if value=='apply' else 'Снимаю'
                lines.append(f'{verb} сценарную гипотезу «{actions.get(fields[1],fields[1])}»'+
                    (f' по наблюдениям {fields[2].upper()}' if fields[2] else '')+'.')
            else:
                _,platform,kind,lang,topic=fields
                verb={'increase':'Повысил','decrease':'Снизил','normal':'Вернул обычный'}[value]
                scope=owner_notify.platform_name(platform)+' / '+{'video':'видео','post':'посты','article':'статьи'}.get(kind,kind)+((' / '+lang.upper()) if lang else '')
                lines.append(f'{verb} вес темы «{topics.get(topic,topic)}» при выборе следующих материалов: {scope}.')
        digest=hashlib.sha256(json.dumps(changes,sort_keys=True).encode()).hexdigest()[:20]
        owner_notify.send('<b>'+html.escape(owner_notify.label(brand))+' · изменил подачу и выбор тем</b>\n'+
            html.escape('\n'.join(lines))+'\nОснование — измеренные результаты; эффект этих правок ещё не подтверждён.',
            key='editorial-policy:'+brand+':'+digest,cooldown=86400)


def current(brand):
    state = load(PATH)
    if not 0 <= time.time() - state.get('measurements_at', 0) <= MAX_AGE:return {}, {}
    return state, state.get('brands', {}).get(brand, {})


def context(brand, platform='youtube', kind='video', lang=''):
    state, plan = current(brand)
    from growth_strategy import context as strategy_context
    strategy = strategy_context(brand, platform, kind, lang)
    if not plan:return {'prompt':strategy['prompt'], 'receipt':{'revision':'strategy:'+strategy['receipt']['trial_id'],'strategy':strategy['receipt']} if strategy['receipt'] else {}}
    # Recheck active factors at consumption, not only when the snapshot was built.
    import growth_brief
    busy = {x.get('factor') for x in growth_brief.active(brand)}
    actions = [x for x in plan['actions'] if kind=='video' and x['id'] in RECIPES and busy.isdisjoint(RECIPES[x['id']][0])]
    receipt = {'revision': state['revision'], 'measurements_at': state['measurements_at'], 'actions': [x['id'] for x in actions]}
    if strategy['receipt']:receipt['strategy'] = strategy['receipt']
    text = '\nEditorial hypotheses to apply to this new script (not new factual evidence): ' + json.dumps([RECIPES[x['id']][1] for x in actions]) if actions else ''
    return {'prompt': text+strategy['prompt'], 'receipt': receipt}


def prompt(brand, **kwargs):
    return context(brand, **kwargs)['prompt']


def record(brand, consumer, receipt, **details):
    if not receipt:return
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = {'at': time.time(), 'brand': brand, 'consumer': consumer, **receipt, **details}
    # O_APPEND single write keeps simultaneous producers' records intact.
    fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:os.write(fd, (json.dumps(line, ensure_ascii=False)+'\n').encode())
    finally:os.close(fd)
    if details.get('run_id') or details.get('source_id'):
        from growth_strategy import record as record_strategy
        record_strategy(brand, details.get('run_id') or details.get('source_id'), receipt)


def choose(brand, candidates, platform='youtube', kind='video', lang='', consumer='source_selection', rng=None):
    """Pick within an already eligible fresh pool. Never makes excluded leads eligible."""
    if not candidates:return None
    state, plan = current(brand)
    if not plan:return None
    rng = rng or random
    cell = plan.get('allocations', {}).get('|'.join((platform, kind, lang)), {})
    groups = cell.get('topics', {})
    weights = [groups.get(classify(brand, c), {}).get('weight', 1) for c in candidates]
    from growth_strategy import ranking_bonus
    research_weights = [ranking_bonus(brand,c,platform,kind,lang) for c in candidates]
    weights = [a*b for a,b in zip(weights,research_weights)]
    # Preserve the existing order unless there is measured evidence to change it.
    if len(candidates) == 1 or len(set(weights)) == 1:
        picked, mode = candidates[0], 'original_order_no_comparable_difference'
    elif rng.random() < EXPLORATION:
        picked, mode = rng.choice(candidates), 'exploration'
    else:
        picked, mode = rng.choices(candidates, weights=weights, k=1)[0], 'measured_topic_allocation'
    record(brand, consumer, {'revision': state['revision']}, platform=platform, kind=kind, lang=lang,
        source_id=picked.get('id') or picked.get('run_id'), topic=classify(brand, picked), mode=mode,
        candidates=len(candidates), weights=weights,research_weights=research_weights)
    return picked


def status():
    import growth_brief
    state = load(PATH)
    report = load(DATA / 'growth_report.json')
    entries = []
    try:
        for line in LOG.read_text().splitlines()[-200:]:
            try:entries.append(json.loads(line))
            except ValueError:pass
    except OSError:pass
    assignments = growth_brief._assignments()
    progress = []
    for e in report.get('experiments', []):
        assigned = [x for x in assignments if x.get('experiment') == e['id']]
        progress.append({**e, 'assigned_test': sum(x['arm']=='test' for x in assigned),
            'assigned_control': sum(x['arm']=='control' for x in assigned)})
    age = time.time()-state.get('measurements_at', 0)
    return {'effective_status': 'active' if state and 0 <= age <= MAX_AGE else 'waiting_for_fresh_measurements',
        'measurement_age_seconds': round(age, 1) if state else None,
        'state': state, 'experiments': progress, 'recent_consumption': entries[-20:]}


def choose_rubric(counts, rng=None):
    """Keep topic diversity; bias only within the two least represented levels."""
    fallback = min(counts, key=lambda r:(counts[r], r))
    from growth_strategy import topic_hint, context as strategy_context
    hint=topic_hint('lvl')
    if hint.get('rubric') in counts and strategy_context('lvl')['receipt']:
        return hint['rubric']
    state, plan = current('lvl')
    if not plan:return fallback
    rng = rng or random
    candidates = [r for r in counts if counts[r] <= min(counts.values())+1]
    allocations = plan.get('allocations', {})
    # Languages are never pooled into a fictitious common audience.
    cell = allocations.get('youtube|video|en') or allocations.get('youtube|video|ru') or {}
    weights = [cell.get('topics', {}).get(r, {}).get('weight', 1) for r in candidates]
    if len(set(weights)) == 1:picked, mode = fallback, 'diversity_insufficient_comparable_data'
    elif rng.random() < EXPLORATION:picked, mode = rng.choice(candidates), 'exploration'
    else:picked, mode = rng.choices(candidates, weights=weights, k=1)[0], 'measured_topic_allocation'
    record('lvl', 'rubric_selection', {'revision': state['revision']}, rubric=picked, mode=mode,
        candidates=candidates, weights=weights)
    return picked


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv)>1 else 'status'
    if command not in ('refresh', 'status'):raise SystemExit('growth_actions.py refresh|status')
    print(json.dumps(refresh(notify=True) if command=='refresh' else status(), ensure_ascii=False, indent=1))
