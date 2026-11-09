"""Persistent editorial trials and autonomous, reversible course corrections.

Direction trials are observational, distinct from the factory's randomised A/B
tests. Their 24–30 h comparisons never mix platforms, formats or languages.
One weak upload cannot retire a direction; missing counters never become zero.
"""
import fcntl
import hashlib
import json
import re
import statistics
import time
from pathlib import Path
from growth_scout import BASE, ROOT as SCOUT, load, save

STATE = BASE/'data/growth_strategy.json'
EVENTS = BASE/'data/growth_strategy_events.jsonl'
ASSIGNMENTS = BASE/'data/growth_strategy_assignments.jsonl'
MIN_SAMPLES = 3
SCOPES = {
    'wrongdev':('youtube|video|','telegram|post|'),
    'migratoria':('youtube|video|','telegram|post|','dzen|article|'),
    'lvl':('youtube|video|','telegram|post|ru','telegram|post|en'),
    'storywobble':('youtube|video|',),
}


def lines(path):
    try:raw = path.read_text().splitlines()
    except OSError:return []
    out = []
    for line in raw[-12000:]:
        try:out.append(json.loads(line))
        except ValueError:pass
    return out


def append(path, value):
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_APPEND|os.O_CREAT|os.O_WRONLY, 0o600)
    try:os.write(fd, (json.dumps(value, ensure_ascii=False)+'\n').encode())
    finally:os.close(fd)


def cohort_rows(now):
    seen, out = set(), []
    for row in load(BASE/'data/content_age_cohorts.json').values():
        metric = row.get('metrics') or {}
        key = (row.get('brand'), row.get('platform'), row.get('kind'), row.get('lang') or '', row.get('url'))
        if (key in seen or not 24 <= (row.get('age_hours') or 0) < 30 or metric.get('stale')
                or metric.get('views') is None or not row.get('published_at')
                or not 0 <= now-row['published_at'] <= 30*86400):continue
        seen.add(key);out.append(row)
    return out


def evaluate(trial, rows, assignments):
    """Actual publication + stable consumer identity is required to attribute a trial."""
    by_run = {(a['brand'], a['run_id'],a.get('cell')):a for a in assignments if a.get('run_id')}
    cells = {}
    for row in rows:
        if row.get('brand') != trial['brand']:continue
        key = '|'.join((row['platform'], row['kind'], row.get('lang') or ''))
        scope=trial.get('scope')
        if scope and (key.split('|')[:2]!=scope.split('|')[:2] or
                (scope.split('|')[2] and key!=scope)):continue
        run=row.get('run_id') or row.get('source_id')
        a = by_run.get((trial['brand'],run,key)) or by_run.get((trial['brand'],run,'|'.join(key.split('|')[:2])+ '|'))
        belongs = a and a.get('trial_id') == trial['id']
        # Compare with earlier publications only, so later pivots cannot rewrite the baseline.
        if not belongs and a:continue  # other direction trials never become controls
        if not belongs and row['published_at']>=trial.get('started_at',float('inf')):continue
        cells.setdefault(key, {'test':[], 'baseline':[]})['test' if belongs else 'baseline'].append(row)
    result = {}
    for key, groups in cells.items():
        total=len(groups['test'])
        test = sorted(groups['test'],key=lambda r:-r['published_at'])[:6]
        base = sorted(groups['baseline'], key=lambda r:-r['published_at'])[:12]
        n, nb = len(test), len(base)
        med = statistics.median(r['metrics']['views'] for r in test) if n else None
        bm = statistics.median(r['metrics']['views'] for r in base) if nb else None
        ratio = (med+1)/(bm+1) if med is not None and bm is not None else None
        decision = 'collecting'
        if n >= MIN_SAMPLES and nb >= MIN_SAMPLES:
            decision = 'develop' if ratio >= 1.25 else 'pivot' if ratio <= .65 else 'iterate'
        elif n >= MIN_SAMPLES:
            decision = 'explore_next'  # completed pilot batch; no baseline, no winner/loser claim
        if decision=='iterate' and total>=6:decision='explore_next'
        result[key] = {'n':n,'baseline_n':nb,'median_views':med,'baseline_median_views':bm,
            'measured_total':total,
            'ratio':ratio,'decision':decision,'urls':[r['url'] for r in test],
            'evidence_type':'same_age_observational_not_causal'}
    return result


def refresh(now=None, notify=True):
    now = time.time() if now is None else now
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with STATE.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load(STATE, {'brands':{}})
        assignments, rows = lines(ASSIGNMENTS), cohort_rows(now)
        changes = []
        for brand in ('wrongdev','migratoria','lvl','storywobble'):
            research = load(SCOUT/(brand+'.json'))
            fresh=0 <= now-research.get('updated_at', 0) <= 7*86400
            research['ideas']=[i for i in (research.get('ideas',[]) if fresh else []) if len(i.get('examples',[]))>=2
                and len({v.get('channel_id') for v in i['examples']})>=2
                and all((v.get('views') or 0)>=10000 for v in i['examples'])]
            plan = state['brands'].setdefault(brand, {'trials':[], 'retired':[]})
            for scope in SCOPES[brand]:
                active = next((t for t in reversed(plan['trials']) if t['status'] == 'active' and t.get('scope')==scope), None)
                if active:
                    retired_id=active['idea']['id']
                    active,events=review_trial(active,rows,assignments,now)
                    changes.extend(events)
                    if active is None:plan['retired'].append(scope+':'+retired_id)
                if active is None:
                    ideas = [i for i in research.get('ideas', []) if scope+':'+i['id'] not in plan['retired']]
                    if ideas:
                        idea = ideas[0]
                        identity = hashlib.sha256(f'{brand}:{scope}:{idea["id"]}:{now}'.encode()).hexdigest()[:16]
                        active = {'id':identity,'brand':brand,'scope':scope,'started_at':now,'status':'active','idea':idea,
                            'measurements':{},'cell_decisions':{}}
                        plan['trials'].append(active)
                        changes.append({'brand':brand,'trial_id':identity,'name':idea['name_ru'],'cell':scope,
                            'action':'start_trial','reason':'new public demand leads; original content hypothesis',
                            'source_urls':[v['url'] for v in idea['examples']]})
            plan['trials'] = plan['trials'][-60:]
        state['updated_at'] = now
        save(STATE, state)
        for event in changes:append(EVENTS, {'at':now, **event})
    if notify and changes:notify_changes(changes)
    return {'updated_at':now,'changes':changes,'brands':state['brands']}


def review_trial(active, rows, assignments, now):
    events=[]
    brand=active['brand']
    active['measurements'] = evaluate(active, rows, assignments)
    # A strong cell can develop its series; a weak cell can only
    # change its own tactic, never overrule another language/site.
    for cell, outcome in active['measurements'].items():
        previous = active.setdefault('cell_decisions', {}).get(cell)
        decision = outcome['decision']
        if decision != 'collecting' and previous != decision:
            active['cell_decisions'][cell] = decision
            events.append({'brand':brand,'trial_id':active['id'],'name':active['idea']['name_ru'],
                'idea_id':active['idea']['id'],'action':decision,'cell':cell,'evidence':outcome})
    # Wait for all assigned publishing cells to mature. Three posts
    # on one site cannot retire a video experiment on another.
    assigned_cells = {a.get('cell') for a in assignments if a.get('trial_id') == active['id']}
    def resolved(cell):
        direct=active.get('cell_decisions',{}).get(cell)
        if direct:return direct in ('pivot','explore_next')
        # A bilingual master is assigned before language variants
        # exist. Require mature results for BOTH expected languages.
        if brand=='lvl' and cell=='youtube|video|':
            return all(active.get('cell_decisions',{}).get('youtube|video|'+lg) in ('pivot','explore_next') for lg in ('ru','en'))
        return False
    if assigned_cells and all(resolved(c) for c in assigned_cells):
        active.update(status='retired',ended_at=now)
        return None,events
    return active,events

def notify_changes(changes):
    import html
    import owner_notify
    words = {'start_trial':'Назначил направление для следующих выпусков','develop':'Развиваю историю/формат',
             'pivot':'Меняю тактику для этой площадки и языка','iterate':'Меняю подачу следующего выпуска',
             'explore_next':'Пилотная серия измерена, проверяю следующее направление без объявления победителя'}
    messages=[]
    for event in changes:
        e = event.get('evidence') or {}
        why = (f'Медиана просмотров в возрасте суток: {e.get("median_views")} против {e.get("baseline_median_views")}; '
               f'выпусков {e.get("n")}/{e.get("baseline_n")}. Это наблюдение, не доказанная причина.' if e else
               'Найдены публичные примеры спроса; проверяю собственную идею, успех ещё не измерен.')
        platform,kind,lang=(event.get('cell') or '||').split('|')
        scope=' · '.join(x for x in (owner_notify.platform_name(platform),
            {'video':'видео','post':'посты','article':'статьи'}.get(kind,kind),lang.upper()) if x)
        messages.append(f'<b>{html.escape(owner_notify.label(event["brand"]))} · {html.escape(scope)}</b>\n'
            f'{words[event["action"]]}: {html.escape(event["name"])}.\n{why}')
    digest=hashlib.sha256(json.dumps(changes,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
    # Notify only when decisions change, in one compact batch, never on every poll.
    owner_notify.send('\n\n'.join(messages)[:3900],key='strategy-batch:'+digest,cooldown=86400)

def eligible_assignment(assignment, now):
    run = assignment.get('run_id')
    if not run:return False
    post = load(BASE/'output/lvl_posts'/run/'post.json')
    if post.get('status') in ('held_for_editorial_revision','rejected'):return False
    video = load(BASE/'output/runs'/run/'manifest.json')
    if video.get('status') in ('held_for_review','rejected','abandoned'):return False
    cartoon = load(BASE/'output/lvl_runs'/run/'manifest.json')
    if cartoon.get('status') in ('held_for_visual_revision','rejected','abandoned'):return False
    if cartoon.get('status') == 'failed':
        from lvl_pipeline import MAX_RENDER_ATTEMPTS, RESUME_WINDOW
        if (not cartoon.get('resumable') or cartoon.get('attempts',1)>=MAX_RENDER_ATTEMPTS
                or now-cartoon.get('failed_at',now)>RESUME_WINDOW):return False
    return True


def context(brand, platform='youtube', kind='video', lang=''):
    now = time.time()
    state = load(STATE)
    if not 0 <= now-state.get('updated_at', 0) <= 86400:return {'prompt':'','receipt':{}}
    if not 0 <= now-load(SCOUT/(brand+'.json')).get('updated_at',0) <= 7*86400:return {'prompt':'','receipt':{}}
    plan = state.get('brands',{}).get(brand,{})
    cell = '|'.join((platform,kind,lang))
    trial = next((t for t in reversed(plan.get('trials',[])) if t['status']=='active' and t.get('scope')==cell), None)
    if not trial:return {'prompt':'','receipt':{}}
    decision = trial.get('cell_decisions',{}).get(cell,'collecting')
    assigned = [a for a in lines(ASSIGNMENTS) if a.get('trial_id')==trial['id'] and a.get('cell')==cell
        and eligible_assignment(a, now)]
    if decision=='collecting' and len(assigned)>=MIN_SAMPLES:
        return {'prompt':'','receipt':{}}  # wait for the three pilot measurements, normal production continues
    idea = trial['idea']
    tactic = {
        'collecting':'Test the original mechanism with a new concrete premise; keep the pilot interpretable.',
        'develop':'Develop this direction as a new episode, with a fresh conflict/question and consequence; do not repeat the previous story.',
        'iterate':'Keep the audience question but change the opening situation and explanation/visual example for the next episode.',
        'explore_next':'Use a new audience question and concrete situation; the pilot batch has no sufficiently established baseline.',
        'pivot':'This cell underperformed. Switch the premise and pacing, use a different concrete case; do not repeat its failed opening.'}[decision]
    prompt = ('\nAutonomous editorial direction (hypothesis, not factual source evidence): '+json.dumps({
        'name':idea['name_ru'],'proposed_mechanism':idea['mechanic_en'] if kind=='video' else
            'Write an original practical text exercise, decision example or reusable worksheet; do not import video scenes, game scores or a video recap.',
        'original_angle':idea['original_angle_en'] if kind=='video' else idea.get('query_ru','')+' / '+idea.get('query_en',''),
        'tactic':tactic,
        'language_specific_observations':trial.get('cell_decisions',{})},ensure_ascii=False)+
        '\nApply only where relevant to the supplied topic. Never invent facts or replace the actual news source. '
        'Existing A/B arm instructions and factual/visual quality gates take precedence. '
        'Make your own scenes, dialogue and characters; do not copy example videos. '
        'Reference metadata do not reveal the source plots.')
    return {'prompt':prompt,'receipt':{'trial_id':trial['id'],'idea_id':idea['id'],'cell':cell,'decision':decision}}


def topic_hint(brand,platform='youtube',kind='video',lang=''):
    state=load(STATE)
    if not 0<=time.time()-state.get('updated_at',0)<=86400:return {}
    if not 0<=time.time()-load(SCOUT/(brand+'.json')).get('updated_at',0)<=7*86400:return {}
    for t in reversed(state.get('brands',{}).get(brand,{}).get('trials',[])):
        if t.get('status')=='active' and t.get('scope')=='|'.join((platform,kind,lang)):return t['idea']
    return {}


def ranking_bonus(brand, candidate,platform='youtube',kind='video',lang=''):
    idea=topic_hint(brand,platform,kind,lang)
    if not idea:return 1.0
    stop={'shorts','video','animated','animation','анимация','короткий','история','about','with','what','your'}
    def stems(text):return {w[:6] for w in re.findall(r'[a-zа-яё]{4,}',text.lower()) if w not in stop}
    query=stems(idea.get('query_ru','')+' '+idea.get('query_en',''))
    actual=stems((candidate.get('title') or '')+' '+(candidate.get('text') or candidate.get('original_text') or '')[:800])
    return 1.6 if len(query&actual)>=2 else 1.0


def record(brand, run_id, receipt):
    value = receipt.get('strategy') or receipt
    if not value.get('trial_id') or not run_id:return
    if any(a.get('brand')==brand and a.get('run_id')==run_id and a.get('cell')==value.get('cell') for a in lines(ASSIGNMENTS)):return
    append(ASSIGNMENTS, {'at':time.time(),'brand':brand,'run_id':str(run_id),**value})


if __name__ == '__main__':
    print(json.dumps(refresh(),ensure_ascii=False,indent=1))
