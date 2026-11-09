"""Writes LVL episodes: topic choice from real demand, a script grounded in fetched evidence, bilingual (RU + EN), fact-reviewed.

An episode is never produced without at least one cited primary source, and nothing here gives personalised medical or
financial advice. Validation is code, not a request to the model."""
import contextlib
import json
import os
import re
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
import lvl_art
import lvl_cast
import lvl_scene
import lvl_topics
import text_llm

BASE_DIR = Path(os.getenv('VIDEO_FACTORY_DIR', Path(__file__).resolve().parent.parent))
MODELS = os.getenv('VIDEO_SCRIPT_MODELS', 'openai/gpt-oss-120b,qwen/qwen3.8-27b').split(',')
MAX_WORDS = {'ru': 88, 'en': 110}   # measured on the production voices (2026-11-01): Russian 0.45 s per word, English 0.34 s; total = speech + about 5.3 s of title, pauses and end card
MIN_WORDS = {'ru': 62, 'en': 76}
TARGET_WORDS = {'ru': 72, 'en': 90}   # models undercount; ask for the middle of the window
BANNED = {
    'ru': r'гарантир|вылеч|без усилий|за одну ночь|разбогатеешь|быстрые деньги|купи акци|вложи все|принимай (?:по )?\d',
    'en': r'guarantee|\bcures?\b|get rich|without effort|overnight|buy (?:this )?stock|put all your|take \d+ ?(?:mg|g|pills)',
}
# The owner ruled the spoken "this is general information, not advice" out (2026-11-01): it reads as noise. Checked in code, because the writer model keeps adding it.
DISCLAIMER_WORDING = {
    'ru': r'общая информация|общая справка|не (?:является )?(?:медицинск\w+ |финансов\w+ |юридическ\w+ )?(?:совет\b|рекомендаци\w*)|консультац\w+ (?:с )?врач|проконсультируйтесь',
    'en': r'general info(?:rmation)?|informational purposes|not (?:medical |financial |legal )?(?:advice|a recommendation)|consult (?:a|your) (?:doctor|physician|professional)',
}
SYSTEM = '''You are the head writer of LVL, a vertical cartoon series: "your life as an RPG". Money, body, brain, energy and habits are skills to level up.
The hero LVL is a lazy, sarcastic, quick-witted purple square who keeps trying to level up and sometimes fails funnily. Opponents are temptations (Sloth, Dopamine,
Procrastination, Burnout) and allies (Piggy). No magic pills, no motivational noise, no shaming. Humour is dry and warm.

Episode structure (35-45 seconds when spoken): 1) HOOK in the first line: a sharp question or absurd true claim, at most 12 words. 2) CONFLICT: LVL meets a temptation or boss
in a concrete situation. 3) USEFUL PART: one or two specific, checkable ideas backed by the EVIDENCE below, said in plain words. 4) FINISH: an XP reward ("+10 XP") or a
funny fail, a callback, and a short invitation to follow for the next level.

Hard rules:
- Use only facts present in EVIDENCE. Every factual claim is listed in "claims" with the evidence id that supports it, restated no stronger than the source (keep
  "may", "on average", "in studies of adults" where the source does). Never invent numbers, studies, quotes or experts.
- No personalised medical or financial advice, no dosages, no named stocks or products, no guarantees, no "cure". General information only. If the topic is about
  health or money, the useful part says what research found. Never voice a disclaimer in any scene ("this is general information, not advice", "consult a doctor" and the like):
  the owner banned them. The final scene is the XP reward or fail plus the invitation to follow.
- The quest or tip LVL tries must restate what the evidence found in the evidence's own terms. Do not add times of day, amounts, durations, food or product names,
  or "rules" that the evidence does not contain ("no coffee after lunch" is invented when the source says "coffee close to bedtime"). A fail or a joke may be invented;
  a fact or a recommendation may not.
- Every spoken sentence is one of two kinds: a listed claim (restated from the evidence) or pure story (a joke, a feeling, an action, a game metaphor) that states no fact about
  health, money or research. Do not dress the story up with outcomes ("wakes up refreshed", "energy all day") that sound like findings the evidence does not contain.
- Each episode has its OWN storyline, setting and joke: never reuse the premise of the recent episodes listed below; vary the hook form (question, bold claim,
  confession, countdown, mistake).
- Spoken text: short sentences, natural speech, no filler, no slogans like "unlock your potential". Numbers are written as words in the spoken text.
- Russian is written first and must sound native; the English line is an adaptation of the same beat in natural English (not a literal translation), about the same length.
- Each scene has one speaker (the character who talks) and one or two actors on screen. Visual fields must use only the allowed values.
Return one JSON object and nothing else.'''
SCHEMA = '''{
 "title_ru": "<=60 chars, curiosity hook, honest", "title_en": "<=60 chars",
 "summary": "one sentence English storyline, used to avoid repeating it later",
 "scenes": [ {
   "speaker": "<cast id>", "text_ru": "...", "text_en": "...",
   "setting": "<one of SETTINGS>", "claim": "<claim id or null>",
   "actors": [ {"id": "<cast id>", "side": "center|left|right", "expression": "<one of EXPRESSIONS>", "action": "none|bounce|shake|hop|cheer|slump|point|spin|flex"} ],
   "props": [ {"icon": "<one of ICONS>", "x": 0.2-0.8, "y": 0.14-0.34, "at": seconds from line start} ],
   "popup": "+10 XP or null", "level_up": false
 } ],
 "claims": [ {"id": "c1", "text_en": "the claim as the evidence supports it", "evidence": "e1"} ],
 "guest": null or {"id": "slug", "name_ru": "...", "name_en": "...", "voice": "hero|lazy|spark|sly|boss|narrator", "role": "...", "personality": "...",
   "spec": {"shape": "squircle|circle|capsule|drop|cloud|battery|coin|blob", "body": "#hex", "accent": "#hex", "outline": "#hex", "belly": "#hex",
            "eyes": "round|dot|visor|big", "accessory": "none|antenna|horns|cat_ears|cap|glasses|headband|crown|halo|sprout", "accessory_color": "#hex"}},
 "description_ru": "2-3 lines for the video description (no hashtags)", "description_en": "same in English",
 "hashtags_ru": ["#..", "#.."], "hashtags_en": ["#..", "#.."]
}'''


class ScriptError(RuntimeError):
    pass


WALL_SECONDS = {'agy': 290, 'openrouter': 170, 'groq': 120}   # per model: a keep-alive stream can otherwise hold a request for many minutes


@contextlib.contextmanager
def wall_clock(seconds):
    """Hard limit for one blocking call (main thread only): raises TimeoutError when it takes longer."""
    def expired(signum, frame):
        raise TimeoutError(f'call exceeded {seconds} s')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


SLOW_FILE = BASE_DIR / 'data' / 'lvl' / 'llm_slow.json'
SLOW_TTL = 1800


def _slow():
    try:return {k: v for k, v in json.loads(SLOW_FILE.read_text()).items() if v > time.time()}
    except (OSError, ValueError):return {}


def _mark_slow(key):
    state = _slow()
    state[key] = time.time() + SLOW_TTL
    try:
        SLOW_FILE.parent.mkdir(parents=True, exist_ok=True)
        SLOW_FILE.write_text(json.dumps(state))
    except OSError:pass


def _llm(messages, temperature=0.7):
    """First approved model that returns a JSON object, each under its own wall-clock limit.

    A model that hangs on these long prompts is skipped by LVL for 30 minutes (own file; the factory's shared cooldowns are not touched)."""
    errors = []
    slow = _slow()
    for provider, model in text_llm.candidates(MODELS):
        if f'{provider}:{model}' in slow:
            errors.append(f'{provider}:{model}: skipped, too slow recently')
            continue
        try:
            with wall_clock(WALL_SECONDS.get(provider, 150)):
                raw = text_llm.call(provider, model, messages, temperature=temperature, limit_file=BASE_DIR / 'data' / 'lvl' / 'llm_limit.json')
            data = json.loads(raw)
        except TimeoutError as exc:
            _mark_slow(f'{provider}:{model}')
            errors.append(f'{provider}:{model}: {exc}')
            continue
        except (text_llm.Unavailable, ValueError) as exc:
            errors.append(f'{provider}:{model}: {str(exc)[:120]}')
            continue
        if isinstance(data, dict):return data, f'{provider}:{model}'
        errors.append(f'{provider}:{model}: not a JSON object')
    raise ScriptError('no text model answered (' + '; '.join(errors or ['all cooling down: ' + text_llm.cooling_summary()]) + ')')


def clean_phrase(chosen, offered):
    """The model often echoes the list line ("[en] phrase (rank 0)"); map it back to the offered phrase so exclusions and topic memory match."""
    text = re.sub(r'\s*\(rank \d+\)\s*$', '', re.sub(r'^\s*[-*]?\s*\[[a-z]{2}\]\s*', '', str(chosen))).strip()
    for p in offered:
        if p.lower() == text.lower():return p
    return text


def pick_topic(rubric=None, exclude=()):
    """Choose what to make: a real search phrase from demand data, not yet covered, in the least-recently-used rubric."""
    history = lvl_topics.used()
    counts = {r: 0 for r in lvl_topics.RUBRICS}
    for h in history[-20:]:counts[h.get('rubric')] = counts.get(h.get('rubric'), 0) + 1
    from growth_actions import choose_rubric
    rubric = rubric or choose_rubric(counts)
    phrases = lvl_topics.demand(rubric)
    # Researched demand adds specific questions to the real suggestion pool;
    # primary-source evidence is still collected and checked independently.
    from growth_scout import ROOT as scout_root, load as scout_load
    researched = scout_load(scout_root/'lvl.json')
    if 0 <= time.time()-researched.get('updated_at',0) <= 7*86400:
        for idea in researched.get('ideas',[]):
            if idea.get('rubric') != rubric:continue
            for language in ('ru','en'):
                query = idea.get('query_'+language,'')
                for rank, phrase in enumerate(lvl_topics.suggest(query,language)[:4]):
                    phrases.append({'phrase':phrase,'lang':language,'seed':query,'rank':rank,'scout_idea':idea['id']})
    if not phrases:raise ScriptError('no demand data (search suggestions unreachable)')
    seen, pool = set(), []
    for p in phrases:
        if p['phrase'] in seen or p['phrase'] in exclude:continue
        seen.add(p['phrase'])
        p['novelty'] = lvl_topics.novelty(p['phrase'], history)
        if p['novelty'] >= 0.55:pool.append(p)
    pool = sorted(pool, key=lambda p: (not bool(p.get('scout_idea')),p['rank'], -p['novelty']))[:40]
    if not pool:raise ScriptError('every candidate phrase is already covered')
    trend = lvl_topics.trending('US', 12)
    msg = (f"Rubric: {rubric} ({lvl_topics.RUBRICS[rubric]['en']}). Pick ONE topic for a 40-second cartoon explainer from these real YouTube search phrases (rank 0 = most searched). "
        "Prefer questions many adults genuinely ask, with a concrete, researchable answer; skip phrases about illness diagnosis, relationships, politics, brands, or anything that "
        "needs personal advice.\nPHRASES:\n" + '\n'.join(f"- [{p['lang']}] {p['phrase']} (rank {p['rank']})" for p in pool) +
        f"\nTRENDING NOW (US, timing hook only, optional): {', '.join(trend[:10])}\nALREADY COVERED: " +
        '; '.join(h.get('title_en', '') for h in history[-12:]) +
        '\nReturn JSON: {"phrase": "the exact chosen phrase", "title_en": "...", "title_ru": "...", "evidence_query_en": "3-5 precise English scientific key terms separated by spaces, no commas, most important first, e.g. '
        '\\"sleep restriction fatigue daytime sleepiness\\" (no questions, no brands)", "broad_query_en": "the same subject in only 2-3 general terms, e.g. \\"sleep deprivation fatigue\\"", "angle": "one sentence: the specific claim the episode will test"}')
    data, model = _llm([{'role': 'system', 'content': 'You choose evidence-friendly topics for a series. Return JSON only.'}, {'role': 'user', 'content': msg}], 0.5)
    if not all(data.get(k) for k in ('phrase', 'title_en', 'title_ru', 'evidence_query_en')):raise ScriptError('topic answer incomplete')
    data['phrase'] = clean_phrase(data['phrase'], [p['phrase'] for p in pool])
    if data['phrase'] not in [p['phrase'] for p in pool]:raise ScriptError('topic phrase was not present in the measured demand pool')
    return {**{k: data[k] for k in ('phrase', 'title_en', 'title_ru', 'evidence_query_en')}, 'broad_query_en': str(data.get('broad_query_en') or ''),
        'angle': data.get('angle', ''), 'rubric': rubric, 'model': model}


def words(text):
    return re.findall(r"[\w'-]+", text or '', re.U)


def validate(data, cast, evidence_ids):
    """Raises ValueError with a message the writer can act on; returns the cleaned episode."""
    scenes = data.get('scenes')
    if not isinstance(scenes, list) or not 5 <= len(scenes) <= 9:raise ValueError(f'need 5-9 scenes, got {len(scenes) if isinstance(scenes, list) else 0}')
    cast_ids = set(cast)
    guest = data.get('guest')
    if guest:
        try:entry = lvl_cast.add_guest(guest, cast);cast_ids.add(entry['spec']['id'])
        except ValueError as exc:raise ValueError('guest character rejected: ' + str(exc))
    claims = {c.get('id'): c for c in data.get('claims') or [] if isinstance(c, dict)}
    if not claims:raise ValueError('at least one claim with an evidence id is required')
    for c in claims.values():
        if c.get('evidence') not in evidence_ids:raise ValueError(f"claim {c.get('id')} cites unknown evidence {c.get('evidence')}")
    used_claims = set()
    clean = []
    for i, sc in enumerate(scenes):
        if sc.get('speaker') not in cast_ids:raise ValueError(f'scene {i + 1}: unknown speaker {sc.get("speaker")}')
        for lang in ('ru', 'en'):
            n = len(words(sc.get('text_' + lang)))
            if not 3 <= n <= 24:raise ValueError(f'scene {i + 1}: text_{lang} has {n} words (3-24)')
            if re.search(BANNED[lang], sc['text_' + lang], re.I):raise ValueError(f'scene {i + 1}: forbidden promise or advice wording in text_{lang}')
            if re.search(DISCLAIMER_WORDING[lang], sc['text_' + lang], re.I):raise ValueError(f'scene {i + 1}: remove the spoken disclaimer from text_{lang}; the series voices none')
            if re.search(r'\d', sc['text_' + lang]) and lang == 'ru':raise ValueError(f'scene {i + 1}: write numbers as words in text_ru')
        if len(words(scenes[0]['text_ru'])) > 14 or len(words(scenes[0]['text_en'])) > 14:raise ValueError('hook (scene 1) must be at most 12 words')
        actors = sc.get('actors') or []
        if not 1 <= len(actors) <= 2:raise ValueError(f'scene {i + 1}: one or two actors')
        if sc['speaker'] not in [a.get('id') for a in actors]:raise ValueError(f'scene {i + 1}: the speaker must be on screen')
        sides = []
        fixed_actors = []
        for a in actors:
            if a.get('id') not in cast_ids:raise ValueError(f'scene {i + 1}: unknown actor {a.get("id")}')
            side = a.get('side') if a.get('side') in ('center', 'left', 'right') else 'center'
            if len(actors) == 2 and side == 'center':side = 'left' if not sides or sides[0] != 'left' else 'right'
            if len(actors) == 1:side = 'center'
            if side in sides:side = 'right' if side == 'left' else 'left'
            sides.append(side)
            fixed_actors.append({'id': a['id'], 'side': side, 'expression': a.get('expression') if a.get('expression') in lvl_art.EXPRESSIONS else 'neutral',
                'action': a.get('action') if a.get('action') in ('none', 'bounce', 'shake', 'hop', 'cheer', 'slump', 'point', 'spin', 'flex') else 'none'})
        props = []
        for p in sc.get('props') or []:
            if p.get('icon') in lvl_scene.ICONS:
                try:props.append({'icon': p['icon'], 'x': max(0.15, min(0.85, float(p.get('x', 0.5)))), 'y': max(0.14, min(0.34, float(p.get('y', 0.22)))),
                    'at': max(0.2, min(5.0, float(p.get('at', 0.8))))})
                except (TypeError, ValueError):pass
        claim = sc.get('claim')
        if claim:
            if claim not in claims:raise ValueError(f'scene {i + 1}: claim {claim} is not listed')
            used_claims.add(claim)
        clean.append({'speaker': sc['speaker'], 'text_ru': re.sub(r'\s+', ' ', sc['text_ru']).strip(), 'text_en': re.sub(r'\s+', ' ', sc['text_en']).strip(),
            'setting': sc.get('setting') if sc.get('setting') in lvl_scene.SETTINGS else 'void', 'claim': claim if claim in claims else None, 'actors': fixed_actors,
            'props': props[:3], 'popup': (str(sc.get('popup'))[:14] if sc.get('popup') else None), 'level_up': bool(sc.get('level_up'))})
    if not used_claims:raise ValueError('no scene references a claim')
    for lang in ('ru', 'en'):
        total = sum(len(words(s['text_' + lang])) for s in clean)
        if not MIN_WORDS[lang] <= total <= MAX_WORDS[lang]:
            aim = TARGET_WORDS[lang]
            raise ValueError(f'{lang} total {total} words, need {MIN_WORDS[lang]}-{MAX_WORDS[lang]}; ' + (f'add about {aim - total} words' if total < aim else f'cut about {total - aim} words') + f' (aim for {aim}) across the scenes and return all scenes again')
    if not any(s['popup'] for s in clean):raise ValueError('the episode needs a +XP popup')
    return {'title_ru': str(data.get('title_ru') or '')[:90], 'title_en': str(data.get('title_en') or '')[:90], 'summary': str(data.get('summary') or '')[:300],
        'scenes': clean, 'claims': [{'id': k, 'text_en': str(v.get('text_en') or '')[:300], 'evidence': v['evidence']} for k, v in claims.items()],
        'guest': (guest or {}).get('id') if guest else None,
        'description_ru': str(data.get('description_ru') or '')[:600], 'description_en': str(data.get('description_en') or '')[:600],
        'hashtags_ru': [h for h in (data.get('hashtags_ru') or []) if isinstance(h, str) and h.startswith('#')][:3],
        'hashtags_en': [h for h in (data.get('hashtags_en') or []) if isinstance(h, str) and h.startswith('#')][:3]}


def review(episode, evidence):
    """Second pass: does each claim stay within what its source says? Returns (ok, problems)."""
    cited = {c['evidence'] for c in episode['claims']}
    src = '\n\n'.join(f"[{e['id']}] {e['title']} ({e['source']}, {e['year']})\n{e['text']}" for e in evidence if e['id'] in cited)
    spoken = '\n'.join(f"{s['speaker']}: {s['text_en']}" for s in episode['scenes'])
    spoken += f"\nTITLE: {episode.get('title_en', '')}\nDESCRIPTION: {episode.get('description_en', '')}"
    msg = ('You are a strict fact-checker for a short cartoon. Check that the SCRIPT only says what the SOURCES support. Flag: a number not in the source, a stronger '
        'claim than the source makes (causal instead of association, "always" instead of "on average"), a claim about a different group or outcome than the source studied (patients vs. healthy adults, '
        'insulin resistance vs. everyday energy), a title or description that promises more than the sources show, advice aimed at one person, a medical or investment recommendation, '
        'or a claim with no source. Do NOT flag jokes, a character\'s own feelings or actions, game metaphors (XP, levels, bosses) or story beats that state no fact about '
        'research, health or money; judge only sentences that present such a fact.\nSOURCES:\n' + src + '\n\nCLAIMS:\n' + '\n'.join(f"{c['id']} ({c['evidence']}): {c['text_en']}" for c in episode['claims']) +
        '\n\nSCRIPT:\n' + spoken + '\n\nReturn JSON: {"ok": true|false, "problems": ["specific problem and how to fix it"]}')
    for _round in range(2):
        data, _model = _llm([{'role': 'system', 'content': 'Return JSON only.'}, {'role': 'user', 'content': msg}], 0.1)
        raw = data.get('problems') or []
        problems = [str(p)[:240] for p in ([raw] if isinstance(raw, str) else raw) if str(p).strip()]
        if problems:return False, problems
        if data.get('ok') is True or str(data.get('ok')).lower() == 'true':return True, []
        msg += '\n\nYour previous answer had no usable verdict. Answer again: "ok": true only if every claim is supported; otherwise "ok": false and "problems" must name each issue.'
    return None, []  # the checker never gave a verdict: the script is not accepted unchecked


def write_episode(topic, evidence, cast=None, previous=None, attempts=3):
    """Returns the validated, fact-reviewed episode dict."""
    cast = cast if cast is not None else lvl_cast.load()
    ids = {e['id'] for e in evidence}
    recent = '\n'.join('- ' + (p.get('summary') or p.get('title_en', '')) for p in (previous if previous is not None else lvl_topics.used())[-6:])
    ev = '\n\n'.join(f"[{e['id']}] {e['title']} — {e['source']} {e['year']} {e['journal']}\n{e['text']}" for e in evidence)
    user = (f"Today: {datetime.now(timezone.utc).date().isoformat()}.\nTOPIC: {topic['title_en']} (rubric {topic['rubric']}; people search: \"{topic['phrase']}\")\nANGLE: {topic.get('angle', '')}\n\n"
        f"CAST (use these ids; you may add ONE new guest character only if the story truly needs it, otherwise guest=null):\n{lvl_cast.describe(cast)}\n\n"
        f"SETTINGS: {', '.join(lvl_scene.SETTINGS)}\nEXPRESSIONS: {', '.join(lvl_art.EXPRESSIONS)}\nICONS: {', '.join(lvl_scene.ICONS)}\n\n"
        f"RECENT EPISODES (do not repeat their premise):\n{recent or '- none yet'}\n\nEVIDENCE (the only source of facts):\n{ev}\n\nJSON SCHEMA:\n{SCHEMA}\n"
        f"Length: all Russian lines together {MIN_WORDS['ru']}-{MAX_WORDS['ru']} words (aim for {TARGET_WORDS['ru']}), all English lines together {MIN_WORDS['en']}-{MAX_WORDS['en']} words "
        f"(aim for {TARGET_WORDS['en']}); that is about 9-11 words per scene, so write full sentences, not slogans. Count the words before answering. 7-9 scenes. Scene 1 is the hook.")
    import growth_actions
    editorial = growth_actions.context('lvl')
    messages = [{'role': 'system', 'content': SYSTEM + editorial['prompt']}, {'role': 'user', 'content': user}]
    last = ''
    for attempt in range(attempts):
        data, model = _llm(messages, 0.8 if attempt == 0 else 0.5)
        try:
            episode = validate(data, cast, ids)
        except ValueError as exc:
            last = str(exc)
            messages += [{'role': 'assistant', 'content': json.dumps(data, ensure_ascii=False)[:6000]}, {'role': 'user', 'content': f'Fix this and return the full JSON again: {exc}'}]
            continue
        ok, problems = review(episode, evidence)
        if ok is None:
            last = 'fact review: the checker gave no usable verdict'
            continue
        if ok:
            episode['model'] = model
            episode['growth_actions'] = editorial['receipt']
            episode['evidence'] = [{k: e[k] for k in ('id', 'source', 'title', 'year', 'journal', 'url')} for e in evidence if e['id'] in {c['evidence'] for c in episode['claims']}]
            growth_actions.record('lvl', 'episode_script', editorial['receipt'], title=episode['title_en'],
                rubric=topic['rubric'], actions_status='included_in_fact_reviewed_script')
            return episode
        last = 'fact review: ' + '; '.join(problems)
        messages += [{'role': 'assistant', 'content': json.dumps(data, ensure_ascii=False)[:6000]},
            {'role': 'user', 'content': 'A fact-checker found problems. Fix every one (weaken or remove the claim, or change the wording) and return the full JSON again:\n- ' + '\n- '.join(problems)}]
    raise ScriptError('episode rejected after ' + str(attempts) + ' attempts: ' + last)
