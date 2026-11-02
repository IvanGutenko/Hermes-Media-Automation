"""Topic demand signals and primary-source evidence for LVL episodes.

Demand: what people actually type into YouTube search (suggest), and what is trending now. Evidence: abstracts and
official pages fetched live (PubMed, Europe PMC, OpenAlex, MedlinePlus, CFPB, CDC). Nothing here writes claims; the
script writer may only use what is returned here, and every claim in a script cites an evidence id."""
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
import requests

BASE_DIR = Path(os.getenv('VIDEO_FACTORY_DIR', Path(__file__).resolve().parent.parent))
DATA = BASE_DIR / 'data' / 'lvl'
PROXY = os.getenv('HTTPS_PROXY') or os.getenv('HTTP_PROXY') or ''
PROXIES = {'http': PROXY, 'https': PROXY} if PROXY else None
UA = {'User-Agent': 'Mozilla/5.0 (compatible; LVL-research/1.0; +https://t.me/WrongDevp)'}

RUBRICS = {
    'money': {'ru': 'Деньги', 'en': 'Money', 'xp': 'Financial XP',
        'seeds_en': ['how to save money', 'why am i always broke', 'how to start investing', 'emergency fund how much', 'how to pay off debt',
            'how to make a budget', 'compound interest explained', 'is it worth', 'subscription spending'],
        'seeds_ru': ['как накопить деньги', 'почему не хватает денег', 'как начать инвестировать', 'финансовая подушка', 'как погасить долг',
            'как вести бюджет', 'сложный процент', 'как перестать тратить']},
    'body': {'ru': 'Тело', 'en': 'Body', 'xp': 'Strength XP',
        'seeds_en': ['how to build muscle', 'how many steps a day', 'do i need to stretch', 'how much protein', 'why does my back hurt from sitting',
            'how often should i workout', 'is walking enough exercise', 'creatine'],
        'seeds_ru': ['как накачать мышцы', 'сколько шагов в день', 'как часто тренироваться', 'сколько белка нужно', 'болит спина от сидения',
            'ходьба или бег', 'как начать заниматься']},
    'brain': {'ru': 'Мозг', 'en': 'Brain', 'xp': 'Mind XP',
        'seeds_en': ['how to focus', 'why cant i focus', 'how to study faster', 'does multitasking work', 'how to remember what you read',
            'phone and attention', 'how to stop doomscrolling', 'deep work'],
        'seeds_ru': ['как сосредоточиться', 'почему не могу сосредоточиться', 'как быстро учить', 'как запоминать', 'как перестать листать ленту',
            'многозадачность', 'как читать больше']},
    'energy': {'ru': 'Энергия', 'en': 'Energy', 'xp': 'Energy XP',
        'seeds_en': ['why am i always tired', 'how much sleep do i need', 'caffeine and sleep', 'how to fall asleep faster', 'what to eat for energy',
            'how much water per day', 'afternoon crash', 'does napping help'],
        'seeds_ru': ['почему я постоянно устаю', 'сколько нужно спать', 'кофе и сон', 'как быстро заснуть', 'что есть для энергии',
            'сколько воды пить', 'сонливость днём', 'дневной сон']},
    'habits': {'ru': 'Привычки', 'en': 'Habits', 'xp': 'Habit XP',
        'seeds_en': ['how to build a habit', 'how to stop procrastinating', 'how long to form a habit', 'how to quit sugar', 'morning routine does it work',
            'how to wake up early', 'willpower', 'how to stop scrolling at night'],
        'seeds_ru': ['как выработать привычку', 'как перестать прокрастинировать', 'сколько формируется привычка', 'как бросить сахар', 'как вставать рано',
            'сила воли', 'как перестать сидеть в телефоне']},
}
CURATED = [  # official pages worth quoting when the topic matches (checked reachable 2026-11-01)
    {'keys': ('emergency', 'savings', 'save money', 'подушк', 'накоп'), 'name': 'CFPB: building an emergency fund', 'url': 'https://www.consumerfinance.gov/an-essential-guide-to-building-an-emergency-fund/'},
    {'keys': ('sleep', 'tired', 'fatigue', 'сон', 'спать', 'уста'), 'name': 'CDC: about sleep', 'url': 'https://www.cdc.gov/sleep/about/index.html'},
]


def _get(url, timeout=25, **kw):
    r = requests.get(url, headers=UA, timeout=timeout, proxies=PROXIES, **kw)
    r.raise_for_status()
    return r


def _load(name, default):
    try:return json.loads((DATA / name).read_text())
    except (OSError, ValueError):return default


def _store(name, value):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = (DATA / name).with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1))
    tmp.replace(DATA / name)


def suggest(query, lang='en'):
    """What YouTube search offers after this prefix (ordered by popularity)."""
    cache = _load('suggest_cache.json', {})
    key = f'{lang}:{query}'
    hit = cache.get(key)
    if hit and time.time() - hit['at'] < 24 * 3600:return hit['items']
    try:
        r = _get('https://suggestqueries.google.com/complete/search', params={'client': 'firefox', 'ds': 'yt', 'hl': lang, 'q': query})
        items = [s for s in r.json()[1] if isinstance(s, str)]
    except (requests.RequestException, ValueError, IndexError):
        return hit['items'] if hit else []
    cache[key] = {'at': time.time(), 'items': items}
    _store('suggest_cache.json', cache)
    return items


def demand(rubric, per_seed=5):
    """Ranked real search phrases for a rubric in both languages: [{'phrase', 'lang', 'seed', 'rank'}]."""
    cfg = RUBRICS[rubric]
    out = []
    for lang, seeds in (('en', cfg['seeds_en']), ('ru', cfg['seeds_ru'])):
        for seed in seeds:
            for rank, phrase in enumerate(suggest(seed, lang)[:per_seed]):
                out.append({'phrase': phrase, 'lang': lang, 'seed': seed, 'rank': rank})
    return out


def trending(geo='US', limit=15):
    """Terms trending right now on Google; used only as a hook for timing, never as evidence."""
    try:
        root = ET.fromstring(_get('https://trends.google.com/trending/rss', params={'geo': geo}).content)
    except (requests.RequestException, ET.ParseError):
        return []
    return [(i.findtext('title') or '').strip() for i in root.iter('item')][:limit]


def _abstract_from_index(index):
    if not index:return ''
    pos = {}
    for word, places in index.items():
        for p in places:pos[p] = word
    return ' '.join(pos[k] for k in sorted(pos))


def _clip(text, n=1400):
    text = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))).strip()
    return text if len(text) <= n else text[:n].rsplit(' ', 1)[0] + '…'


def pubmed(term, limit=4, years=12):
    """Reviews, meta-analyses and trials from PubMed with abstracts."""
    q = f'({term}) AND (review[pt] OR meta-analysis[pt] OR randomized controlled trial[pt] OR systematic[sb])'
    ids = _get('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi', params={'db': 'pubmed', 'term': q, 'retmax': limit * 2, 'retmode': 'json',
        'sort': 'relevance', 'datetype': 'pdat', 'reldate': years * 365}).json()['esearchresult']['idlist']
    if not ids:return []
    root = ET.fromstring(_get('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi', params={'db': 'pubmed', 'id': ','.join(ids), 'retmode': 'xml'}).content)
    out = []
    for art in root.iter('PubmedArticle'):
        pmid = art.findtext('.//PMID')
        abstract = ' '.join(''.join(t.itertext()) for t in art.iterfind('.//Abstract/AbstractText'))
        if len(abstract) < 200:continue
        year = art.findtext('.//JournalIssue/PubDate/Year') or (art.findtext('.//JournalIssue/PubDate/MedlineDate') or '')[:4]
        kinds = [p.text for p in art.iterfind('.//PublicationType') if p.text]
        out.append({'source': 'PubMed', 'title': _clip(''.join(art.find('.//ArticleTitle').itertext()), 300), 'year': year,
            'journal': art.findtext('.//Journal/Title') or '', 'kind': next((k for k in kinds if 'Review' in k or 'Trial' in k or 'Meta' in k), ''),
            'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/', 'text': _clip(abstract)})
    return out[:limit]


def europepmc(term, limit=3):
    r = _get('https://www.ebi.ac.uk/europepmc/webservices/rest/search', params={'query': f'({term}) AND (PUB_TYPE:"review" OR PUB_TYPE:"systematic review")', 'resultType': 'core',
        'format': 'json', 'pageSize': limit * 3}).json()
    out = []
    for item in r.get('resultList', {}).get('result', []):
        text = _clip(item.get('abstractText'))
        if len(text) < 200:continue
        pid = item.get('pmid') or item.get('id')
        out.append({'source': 'Europe PMC', 'title': _clip(item.get('title'), 300), 'year': str(item.get('pubYear') or ''), 'journal': item.get('journalTitle') or '',
            'kind': 'review', 'url': f'https://europepmc.org/article/{item.get("source", "MED")}/{pid}', 'text': text})
    return out[:limit]


def openalex(term, limit=3):
    r = _get('https://api.openalex.org/works', params={'search': term, 'filter': 'has_abstract:true,type:article,from_publication_date:2012-01-01',
        'per-page': limit * 3, 'select': 'title,publication_year,doi,abstract_inverted_index,cited_by_count,primary_location'}).json()
    out = []
    for w in r.get('results', []):
        text = _clip(_abstract_from_index(w.get('abstract_inverted_index')))
        if len(text) < 200:continue
        src = ((w.get('primary_location') or {}).get('source') or {}).get('display_name') or ''
        out.append({'source': 'OpenAlex', 'title': _clip(w.get('title'), 300), 'year': str(w.get('publication_year') or ''), 'journal': src, 'kind': f"cited {w.get('cited_by_count')}x",
            'url': w.get('doi') or '', 'text': text})
    return out[:limit]


def medlineplus(term, limit=1):
    root = ET.fromstring(_get('https://wsearch.nlm.nih.gov/ws/query', params={'db': 'healthTopics', 'term': term, 'retmax': limit}).content)
    out = []
    for doc in root.iter('document'):
        fields = {c.get('name'): ''.join(c.itertext()) for c in doc.iter('content')}
        text = _clip(fields.get('FullSummary') or fields.get('snippet'))
        if len(text) > 150:out.append({'source': 'MedlinePlus (NIH)', 'title': _clip(fields.get('title'), 200), 'year': '', 'journal': 'MedlinePlus', 'kind': 'health topic summary',
            'url': doc.get('url') or '', 'text': text})
    return out[:limit]


def page(entry):
    """Plain paragraph text of a curated official page."""
    body = _get(entry['url']).text
    body = re.sub(r'(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>', ' ', body)
    paras = [_clip(p, 600) for p in re.findall(r'(?is)<p[^>]*>(.*?)</p>', body)]
    paras = [p for p in paras if len(p) > 80]
    return {'source': entry['name'].split(':')[0], 'title': entry['name'], 'year': '', 'journal': '', 'kind': 'official guidance', 'url': entry['url'], 'text': ' '.join(paras[:5])[:1500]}


STOP = {'with', 'from', 'that', 'this', 'what', 'does', 'effect', 'effects', 'adults', 'people', 'about', 'between', 'their', 'have', 'and', 'the', 'for'}


def keywords(query):
    query = query.replace(',', ' ')
    return sorted({w[:6] for w in re.findall(r'[a-z0-9]{4,}', query.lower()) if w not in STOP})


def relevance(item, words):
    """Share of the query's key terms present (by 6-letter stem) in the item's title and abstract."""
    if not words:return 1.0
    hay = (item['title'] + ' ' + item['text']).lower()
    return sum(1 for w in words if w in hay) / len(words)


def evidence(query, rubric, limit=6, min_relevance=0.6):
    """Search with the full query; if nothing relevant comes back, retry with its first three terms (the writer puts the key terms first)."""
    items, errors = _evidence(query, rubric, limit, min_relevance)
    terms = query.split()
    if len(items) < 2 and len(terms) > 3:
        more, errors2 = _evidence(' '.join(terms[:3]), rubric, limit, min_relevance)
        items = (items + [m for m in more if m['title'] not in {i['title'] for i in items}])[:limit]
        errors.update(errors2)
        for n, it in enumerate(items, 1):it['id'] = f'e{n}'
    return items, errors


def _evidence(query, rubric, limit, min_relevance):
    """Evidence items for a topic: [{'id': 'e1', source, title, year, url, text}]. Sources that fail are skipped, not faked."""
    health = rubric in ('body', 'energy', 'brain', 'habits')
    steps = ([pubmed, europepmc, medlineplus] if health else [openalex, europepmc]) + ([openalex] if health and rubric in ('brain', 'habits') else [])
    items, errors = [], {}
    for fn in steps:
        try:items.extend(fn(query))
        except (requests.RequestException, ET.ParseError, ValueError, KeyError) as exc:errors[fn.__name__] = type(exc).__name__
        if len(items) >= limit:break
    low = query.lower()
    for entry in CURATED:
        if any(k in low for k in entry['keys']) and len(items) < limit + 2:
            try:items.append(page(entry))
            except (requests.RequestException, ValueError):errors['curated'] = 'unreachable'
    words = keywords(query)
    for it in items:it['relevance'] = round(relevance(it, words), 2)
    items = sorted((i for i in items if i['relevance'] >= min_relevance or i['kind'] == 'official guidance'), key=lambda i: -i['relevance'])
    seen, final = set(), []
    for it in items:
        key = re.sub(r'\W+', '', it['title'].lower())[:60]
        if key in seen or not it['text']:continue
        seen.add(key);final.append(it)
    for i, it in enumerate(final[:limit], 1):it['id'] = f'e{i}'
    return final[:limit], errors


# --- topic memory -----------------------------------------------------------------------------------------

def used():
    return _load('topics.json', [])


def tokens(text):
    return {w for w in re.findall(r'[a-zа-я0-9]{4,}', text.lower())}


def novelty(title, history=None):
    """1.0 for a brand-new topic down to 0.0 for one already covered (token overlap with past titles)."""
    mine = tokens(title)
    if not mine:return 0.0
    best = 0.0
    for old in history if history is not None else used():
        theirs = tokens(old.get('title_en', '') + ' ' + old.get('title_ru', ''))
        if theirs:best = max(best, len(mine & theirs) / len(mine | theirs))
    return round(1 - best, 2)


def record(topic):
    items = used()
    items.append({**topic, 'recorded_at': time.time()})
    _store('topics.json', items[-400:])
