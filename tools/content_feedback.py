"""Timestamped measurements and cautious editorial feedback for every live target."""
import hashlib
import copy
import html
import json
import re
import sqlite3
import statistics
import subprocess
import time
from datetime import datetime,timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from script_generator import BASE_DIR,DB_PATH,PROXIES
from youtube_uploader import save
from browser_process import run as run_browser

DATA=BASE_DIR/'data'


def number(value):
    from metrics_numbers import parse_count
    return parse_count(value)


def inventory():
    from migratoria_editor import db
    items=[]
    with sqlite3.connect(f'file:{DB_PATH}?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        for r in con.execute("SELECT id,title,key_topic,processed_text,target_msg_id,posted_date FROM posts WHERE status='posted' AND target_msg_id IS NOT NULL"):
            try:
                stamp=datetime.fromisoformat(r['posted_date'])
                stamp=(stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp()
            except (ValueError,TypeError):stamp=None
            items.append({'brand':'wrongdev','platform':'telegram','kind':'post','source_id':'post-'+str(r['id']),
                'title':r['title'],'topic':r['key_topic'],'url':'https://t.me/WrongDevp/'+str(r['target_msg_id']),
                'published_at':stamp,'message_id':r['target_msg_id']})
    with db() as con:
        for r in con.execute('SELECT * FROM posts WHERE telegram_id IS NOT NULL'):
            items.append({'brand':'migratoria','platform':'telegram','kind':'post','source_id':'migration-post-'+str(r['id']),
                'title':r['title'],'url':'https://t.me/Migratoriya/'+str(r['telegram_id']),
                'published_at':r['published_at'],'message_id':r['telegram_id']})
    for path in (BASE_DIR/'output/runs').glob('*/manifest.json'):
        m=json.loads(path.read_text())
        if m.get('status')!='published' or not m.get('youtube_url'):continue
        script=json.loads((path.parent/'script.json').read_text())
        receipts=list((BASE_DIR/'output/studio_uploads').glob('*_'+m.get('sha256','missing')+'.json'))
        proof=json.loads(receipts[0].read_text()) if receipts else {}
        items.append({'brand':m.get('brand','wrongdev'),'platform':'youtube','kind':'video','run_id':m['run_id'],
            'source_id':('migration-post-' if m.get('brand')=='migratoria' else 'post-')+str(m['source_post_id']),
            'title':script['youtube_meta']['title'],'url':m['youtube_url'],
            'published_at':proof.get('published_at',proof.get('verified_at',m.get('completed_at'))),
            'voice':script.get('narration_voice') or ('Holt' if m.get('audio_mode')=='vids' else m.get('voice','unknown')),'duration':m.get('duration')})
    for path in (DATA/'distribution').glob('*.json'):
        if not re.fullmatch(r'(wrongdev|migratoria)_(vk|reddit|instagram|tiktok)_[a-f0-9]{64}\.json',path.name):continue
        p=json.loads(path.read_text())
        if p.get('status')!='published' or not p.get('url'):continue
        brand,platform=path.stem.split('_')[:2]
        items.append({'brand':brand,'platform':platform,'kind':'video' if p.get('source_id','').startswith('video-') else 'post',
            'source_id':p['source_id'],'title':p.get('payload',{}).get('title'),
            'url':p['url'],'published_at':p.get('confirmed_at')})
    for path in (BASE_DIR/'output/articles').glob('*/article.json'):
        a=json.loads(path.read_text())
        if a.get('status')=='published':
            receipt=json.loads((path.parent/'dzen_receipt.json').read_text())
            items.append({'brand':'migratoria','platform':'dzen','kind':'article','title':a['title'],'url':a['url'],
                'source_id':'migration-post-'+str(a['source_post_id']),'published_at':receipt.get('public_verified_at')})
    # LVL series: every confirmed publication of every episode, per platform and language.
    for path in (BASE_DIR/'output/lvl_runs').glob('*/manifest.json'):
        try:
            m=json.loads(path.read_text());episode=json.loads((path.parent/'script.json').read_text())['episode']
        except (OSError,ValueError,KeyError):continue
        for key,receipt in (m.get('published') or {}).items():
            platform,lang=key.split(':')
            items.append({'brand':'lvl','platform':platform,'kind':'video','lang':lang,'run_id':m['run_id'],'source_id':'lvl-'+m['run_id']+'-'+lang,
                'title':episode.get('title_'+lang),'url':receipt['url'],'published_at':receipt.get('at'),'duration':(m.get('gates',{}).get(lang) or {}).get('duration')})
    # Reconciled native submissions can predate the common distributor receipt.
    known={item['url'] for item in items}
    runs={item.get('run_id'):item['brand'] for item in items if item.get('run_id')}
    for path in (BASE_DIR/'output').glob('native_reddit_*.json'):
        if not re.fullmatch(r'native_reddit_[a-f0-9]{64}\.json',path.name):continue
        proof=json.loads(path.read_text());source=proof.get('source_id','')
        if proof.get('status')!='published' or not proof.get('public_verified_at') or not proof.get('url') or proof['url'] in known:continue
        brand=runs.get(source.removeprefix('video-')) if source.startswith('video-') else ('migratoria' if source.startswith('migration-post-') else 'wrongdev')
        if not brand:continue
        items.append({'brand':brand,'platform':'reddit','kind':'video' if source.startswith('video-') else 'post',
            'source_id':source,'title':proof.get('title'),'url':proof['url'],
            'published_at':proof.get('publication_attempted_at',proof['public_verified_at'])})
        known.add(proof['url'])
    return items


def telegram_metrics(brand,handle):
    r=requests.get('https://t.me/s/'+handle,proxies=PROXIES,timeout=35);r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser');result={}
    for node in soup.select('.tgme_widget_message[data-post]'):
        key='https://t.me/'+node['data-post'];view=node.select_one('.tgme_widget_message_views')
        reactions=[]
        for reaction in node.select('.tgme_reaction'):
            text=reaction.get_text(' ',strip=True);match=re.search(r'[\d.,]+\s*[KM]?',text,re.I)
            if match:reactions.append(number(match[0]) or 0)
        stamp=node.select_one('time[datetime]')
        result[key]={'views':number(view.get_text()) if view else None,
            'reactions':sum(reactions) if reactions else None,
            'photo':bool(node.select_one('.tgme_widget_message_photo_wrap')),
            'source':'public_telegram_widget','observed_at':time.time()}
        if stamp:result[key]['published_at']=datetime.fromisoformat(stamp['datetime']).timestamp()
    return result


def editorial_brief(brand):
    from growth_brief import prompt
    return _feedback_brief(brand)+prompt(brand)


def _feedback_brief(brand):
    path=DATA/'content_feedback.json'
    if not path.exists():return ''
    data=json.loads(path.read_text())
    if time.time()-data.get('updated_at',0)>86400:return ''
    info=data.get('brands',{}).get(brand,{})
    compact={'guidance':info.get('guidance'),'voice_pilot':info.get('voice_pilot',[]),
        'same_age_comparisons':[{**group,'top':group.get('top',[])[:1]} for group in info.get('same_age_comparisons',[])],
        'platform_observations':{platform:[{'title':(row.get('title') or '')[:90],
            'age_hours':round(row['age_hours'],1) if row.get('age_hours') is not None else None,
            'metrics':row['metrics']} for row in rows[:1]]
            for platform,rows in info.get('platform_observations',{}).items()},
        'measurement_errors':list(data.get('errors',{}))}
    try:
        diagnosis=json.loads((DATA/'growth_diagnosis.json').read_text())['brands'].get(brand,{})
        if diagnosis.get('status')=='ok':compact['diagnosis']={'baseline_median_views':diagnosis['baseline']['median_views'],'videos_in_baseline':diagnosis['baseline']['mature_videos'],
            'hypotheses_to_consider':diagnosis.get('hypotheses',[])[:4]}
    except (OSError,ValueError,KeyError):pass
    return '\nPrivate audience feedback (hypotheses, never factual source evidence): '+json.dumps(compact,ensure_ascii=False)


def analyze(items,current=None):
    brands={}
    for brand in ('wrongdev','migratoria','lvl'):
        recent=[x for x in items if x['brand']==brand and x.get('metrics',{}).get('views') is not None]
        # Compare content at the same age/platform/kind. Missing is never zero.
        eligible=[x for x in recent if x.get('age_hours') is not None and 24<=x['age_hours']<=30]
        groups={}
        for x in eligible:groups.setdefault((x['platform'],x['kind']),[]).append(x)
        comparisons=[]
        for (platform,kind),rows in groups.items():
            ranked=sorted(rows,key=lambda x:x['metrics']['views'],reverse=True)
            comparisons.append({'platform':platform,'kind':kind,'count':len(rows),
                'median_views':statistics.median(x['metrics']['views'] for x in rows),
                'top':[{'title':x.get('title'),'views':x['metrics']['views'],'url':x['url']} for x in ranked[:3]],
                'status':'exploratory' if len(rows)<10 else 'observational_comparison'})
        brands[brand]={'measured_items':len(recent),'same_age_comparisons':comparisons,
            'guidance':'Недостаточно сопоставимых данных для выбора победителя. Продолжать разнообразные практические темы и собирать замеры одного возраста.' if not comparisons or all(x['count']<10 for x in comparisons) else
                'Использовать темы лидирующих сопоставимых публикаций как гипотезы; сохранять разнообразие. Просмотры сами по себе не доказывают влияние голоса.'}
        experiments={}
        for item in eligible:
            if item['platform']!='youtube' or not item.get('voice'):continue
            experiments.setdefault(item['voice'],[]).append(item)
        brands[brand]['voice_pilot']=[{'voice':voice,'videos_24h':len(rows),
            'median_views':statistics.median(x['metrics']['views'] for x in rows),
            'median_percentage_viewed':statistics.median(values) if (values:=[x['metrics']['average_percentage_viewed'] for x in rows if x['metrics'].get('average_percentage_viewed') is not None]) else None,
            'status':'exploratory_no_winner' if len(rows)<6 or len(values)<6 else 'compare_retention_and_topic_before_scaling'}
            for voice,rows in experiments.items()]
        # Preserve each platform's meaning and age. Dzen impressions and Reddit
        # votes must be visible to the editor without being ranked as views.
        observed=[x for x in (current or items) if x['brand']==brand and x.get('metrics')]
        platforms={}
        for platform in ('telegram','youtube','vk','dzen','reddit','instagram','tiktok'):
            rows=[x for x in observed if x['platform']==platform]
            if not rows:continue
            latest=sorted(rows,key=lambda x:x.get('published_at') or 0,reverse=True)[:4]
            platforms[platform]=[{'title':x.get('title'),'url':x['url'],'age_hours':x.get('age_hours'),
                'metrics':{k:v for k,v in x['metrics'].items() if k in ('views','reactions','impressions','read_completions',
                    'feed_ctr_percent','likes','comments','shares','score','average_percentage_viewed','observed_at','stale')}} for x in latest]
        brands[brand]['platform_observations']=platforms
    return brands


def record_cohorts(history,items):
    for item in items:
        age=item.get('age_hours');measurement=item.get('metrics',{})
        if age is None or not measurement:continue
        for hours in (6,24,168):
            if not hours<=age<hours+6:continue
            key=item['url']+'@'+str(hours)
            if key not in history:
                history[key]=copy.deepcopy(item);continue
            old=history[key]
            # Studio can expose retention after its first view count. Keep the
            # first count and timestamp; add only the missing retention while
            # the new measurement is still inside this same age window.
            if (item['platform']=='youtube' and not measurement.get('stale')
                    and old['metrics'].get('average_percentage_viewed') is None
                    and measurement.get('average_percentage_viewed') is not None):
                old['metrics'].update(average_percentage_viewed=measurement['average_percentage_viewed'],
                    retention_source=measurement.get('retention_source'),
                    retention_observed_at=measurement['observed_at'],retention_age_hours=age)
    return history


SERIES=DATA/'metrics_series.jsonl'
OVERVIEW=DATA/'youtube_overview.json'
OVERVIEW_KEYS=('stayed_to_watch_pct','average_view_duration_s','average_percentage_viewed','subscribers_gained','traffic_sources','engaged_views_pct')


def youtube_session(configs):
    """Read-only Data API session from any brand token that exists. Public counters of any public video
    are readable with it, so one token covers every channel."""
    from youtube_uploader import get_session
    last=None
    for cfg in configs.values():
        token=Path(cfg.get('youtube_token','/nonexistent'))
        if token.is_file():
            try:return get_session(token)
            except Exception as exc:last=exc
    raise RuntimeError('No usable YouTube token: '+(type(last).__name__ if last else 'none configured'))


def api_metrics(items,configs,now):
    """Views, likes, comments, privacy of every YouTube item plus each channel's subscriber count; no browser."""
    import yt_stats
    session=youtube_session(configs)
    ids=[yt_stats.video_id(x['url']) for x in items if x['platform']=='youtube']
    metrics=yt_stats.videos(session,ids,now)
    subscribers=yt_stats.channels(session,[c.get('youtube_channel_id') for c in configs.values()],now)
    series={url:{k:m.get(k) for k in ('views','likes','comments')} for url,m in metrics.items()}
    for channel,info in subscribers.items():series['channel:'+channel]={k:info.get(k) for k in ('subscribers','views','videos')}
    return metrics,subscribers,series


def load_overview():
    try:return json.loads(OVERVIEW.read_text())
    except (OSError,ValueError):return {}


def store_overview(store,observed,items,now):
    """Keep every Studio overview snapshot with the video's age at that moment (history capped)."""
    ages={x['url']:(x.get('published_at')) for x in items}
    for url,m in observed.items():
        if not m.get('overview_observed_at'):continue
        entry=store.setdefault(url,{'snapshots':[]})
        published=ages.get(url)
        snapshot={'observed_at':m['overview_observed_at'],'age_hours':round((m['overview_observed_at']-published)/3600,2) if published else None,
            **{k:m[k] for k in OVERVIEW_KEYS if k in m}}
        entry['snapshots']=(entry['snapshots']+[snapshot])[-12:]
    return store


MIN_OVERVIEW_AGE_HOURS=20


def overview_targets(items,store,now,limit=8,skip=frozenset()):
    """YouTube videos whose Studio overview is due: new ones first, then by age-dependent refresh interval."""
    due=[]
    for item in items:
        if item['platform']!='youtube' or not item.get('published_at') or item['url'] in skip:continue
        age=(now-item['published_at'])/3600
        # Studio answers "not exposed yet" for retention on fresh Shorts (seen on 2026-11-01 at 10-25 h old).
        if age<MIN_OVERVIEW_AGE_HOURS:continue
        entry=store.get(item['url'],{})
        snapshots=entry.get('snapshots',[])
        last=max([x['observed_at'] for x in snapshots]+[entry.get('last_attempt',0)],default=0)
        interval=5*3600 if age<48 else 24*3600 if age<168 else 72*3600
        if age>24*21 and snapshots:continue
        if now-last>=interval:due.append((last,age,item['url']))
    due.sort(key=lambda row:(row[0]>0,row[0],row[1]))
    return [url for _,_,url in due[:limit]]


def latest_overview(store,url):
    """Newest value of every overview key: the API snapshot of an old video must not hide Studio's 'stayed to watch'."""
    merged={}
    for snapshot in sorted(store.get(url,{}).get('snapshots',[]),key=lambda x:x['observed_at']):
        merged.update({k:v for k,v in snapshot.items() if k in OVERVIEW_KEYS})
    return merged


API_MIN_AGE_HOURS=72   # Analytics API data lags 48-72 h (official docs); younger videos are read from Studio
API_PER_TICK=10


def api_channels(configs,have=None):
    import yt_analytics
    have=set(yt_analytics.channels() if have is None else have)
    return {b for b,c in configs.items() if c.get('youtube_channel_id') in have}


def api_videos(items,configs,now,brands):
    """YouTube items the Analytics API answers for (token stored for their channel, old enough)."""
    return [x for x in items if x['platform']=='youtube' and x.get('brand') in brands and x.get('published_at')
        and (now-x['published_at'])/3600>=API_MIN_AGE_HOURS]


def api_overview(items,configs,store,now,fetch=None,have=None):
    """Per-video retention numbers from the Analytics API: no browser, no Studio page. Returns (values, errors);
    marks every attempt in the store so an empty answer is not asked again within the interval."""
    try:brands=api_channels(configs,have)
    except Exception:return {},{}
    if not brands:return {},{}
    if fetch is None:
        import yt_analytics;fetch=yt_analytics.video_overview
    import yt_stats
    due=[]
    for item in api_videos(items,configs,now,brands):
        entry=store.get(item['url'],{})
        interval=(24 if (now-item['published_at'])/3600<24*7 else 72)*3600
        if now-entry.get('api_attempt',0)>=interval:due.append((entry.get('api_attempt',0),item))
    values={};errors={}
    for _,item in sorted(due,key=lambda row:row[0])[:API_PER_TICK]:
        store.setdefault(item['url'],{'snapshots':[]})['api_attempt']=now
        video=yt_stats.video_id(item['url'])
        try:found=fetch(configs[item['brand']]['youtube_channel_id'],video,item['published_at'],now)
        except Exception as exc:
            errors['yt_analytics']=type(exc).__name__+': '+str(exc)[:160];break  # one refused call (scope, quota) would repeat for every video
        if found:values[item['url']]=found
    return values,errors


PLATFORM_INTERVAL_HOURS={'vk':3,'dzen':6,'reddit':6,'tiktok':6,'instagram':6}  # signed-in pages read in the browser
PASS_PLATFORMS_MAX=2     # tabs per hourly pass: each site is a heavy single-page app on a 4.9 GB host
CALM_WAIT_SECONDS=300    # how long a pass waits for memory/disk pressure to drop before leaving its reading to the next hour
FORCE_AFTER_SECONDS=6*3600  # a host that never calms down must still be measured now and then
PASS_STATE=DATA/'browser_pass_state.json'


def carry_limit(platform):
    """How long the last reading of an item stays visible (marked stale) while its platform waits for its next turn."""
    return 2*PLATFORM_INTERVAL_HOURS[platform]*3600 if platform in PLATFORM_INTERVAL_HOURS else 4*3600


def carry_over(previous_items,metrics,now,only=None):
    for old in previous_items:
        measurement=old.get('metrics',{})
        if old['url'] in metrics or not measurement:continue
        if only is not None and old['platform'] not in only:continue
        if now-measurement.get('observed_at',0)<carry_limit(old['platform']):metrics[old['url']]={**measurement,'stale':True}


def due_platforms(items,state,now,limit=PASS_PLATFORMS_MAX):
    """Oldest-read first. A platform that failed its last reading is retried after an hour, not at once."""
    present={x['platform'] for x in items}
    due=[]
    for name,hours in PLATFORM_INTERVAL_HOURS.items():
        if name not in present:continue
        entry=state.get(name,{})
        wait=hours*3600 if entry.get('ok') else min(hours,1)*3600
        if now-entry.get('tried',0)>=wait:due.append((entry.get('tried',0),name))
    return [name for _,name in sorted(due)[:limit]]


def collect_browser(configs,items,overview,now):
    """Run the in-browser collector (waits for the lease). Returns (observed, errors); never raises.

    The browser is only woken when something is due, a few platforms at a time, and only while the host is not
    short of memory or disk bandwidth (see host_load): the old pass opened Studio plus five sites every hour and
    coincided with the multi-minute stalls of this 4.9 GB, spinning-disk host."""
    folder=BASE_DIR/'output/content_analytics';folder.mkdir(parents=True,exist_ok=True)
    errors={};observed={}
    try:
        state=json.loads(PASS_STATE.read_text()) if PASS_STATE.exists() else {}
        try:covered={x['url'] for x in api_videos(items,configs,now,api_channels(configs))}
        except Exception:covered=set()
        targets=overview_targets(items,overview,now,skip=covered)
        platforms=due_platforms(items,state,now)
        if not targets and not platforms:return observed,errors  # nothing due: leave the browser alone
        import host_load
        forced=now-state.get('last_pass_at',0)>FORCE_AFTER_SECONDS
        reason=None if forced else host_load.wait_calm(CALM_WAIT_SECONDS)
        if reason:
            state['skipped']={'at':now,'reason':reason,'due':platforms,'overview_targets':len(targets)}
            save(PASS_STATE,state);return observed,errors
        save(folder/'browser_job.json',{'items':items,'brands':configs,'overview_targets':targets,
            'lease_wait':1000,'platforms':platforms,'force':forced})
        started=time.time()
        result=run_browser(['docker','exec','video_factory_auth','python3','-u','/app/scripts/media_metrics_browser.py',
            '/data/output/content_analytics/browser_job.json'],capture_output=True,text=True,timeout=1400)
        if result.returncode:
            errors['browser']='Metrics collection incomplete: '+result.stderr[-400:]
            partial=folder/'browser_latest.json'
            if partial.exists():
                candidate=json.loads(partial.read_text())
                if candidate.get('started_at',0)>=started:observed=candidate
        else:observed=json.loads(result.stdout.strip().splitlines()[-1])
        if observed:
            observed['attempted']=targets  # a Studio page that was not ready is not retried every hour
            for name in observed.get('ran',[]):state[name]={'tried':now,'ok':name not in observed.get('errors',{})}
            state['last_pass_at']=now;state.pop('skipped',None)
            if observed.get('skipped'):state['skipped']={'at':now,'reason':'; '.join(observed['skipped'])[:300]}
            save(PASS_STATE,state)
    except Exception as exc:errors['browser']=type(exc).__name__+': '+str(exc)[:200]
    return observed,errors


class exclusive:
    """Serialises the merge-and-write part of the 15-minute tick and the hourly browser pass."""
    def __enter__(self):
        import fcntl
        DATA.mkdir(exist_ok=True)
        self.file=(DATA/'.analytics.lock').open('a')
        fcntl.flock(self.file,fcntl.LOCK_EX)
    def __exit__(self,*exc):self.file.close()


def tick(browser=False):
    """Every 15 minutes: counters through APIs and public pages, no browser lease needed.
    With browser=True (hourly): additionally read what only the signed-in browser can show."""
    configs=json.loads((DATA/'media_brands.json').read_text())
    pre=None
    if browser:pre=collect_browser(configs,inventory(),load_overview(),time.time())
    with exclusive():_tick(configs,pre)


def _tick(configs,pre=None):
    folder=BASE_DIR/'output/content_analytics';folder.mkdir(parents=True,exist_ok=True)
    items=inventory();metrics={};errors={}
    now=time.time()
    previous=json.loads((folder/'latest.json').read_text()) if (folder/'latest.json').exists() else {}
    for brand,cfg in configs.items():
        handle=(cfg.get('telegram') or '').lstrip('@')
        if not handle:continue
        try:metrics.update(telegram_metrics(brand,handle))
        except Exception as exc:errors[brand+'/telegram']=type(exc).__name__
    series={}
    try:
        api,channels,api_series=api_metrics(items,configs,now)
        metrics.update(api);series.update(api_series)
        known={x['url'] for x in items}
        for url,m in api.items():
            if url not in known:
                brand=next((b for b,c in configs.items() if c.get('youtube_channel_id')==m.get('channel_id')),None)
                if brand:items.append({'brand':brand,'platform':'youtube','kind':'video','url':url,'title':m.get('title'),
                    'published_at':None,'voice':'unknown'})
    except Exception as exc:
        channels={};errors['youtube_api']=type(exc).__name__+': '+str(exc)[:200]
    for url,m in metrics.items():
        if m.get('platform') in (None,'telegram') and m.get('views') is not None and 't.me/' in url:series[url]={'views':m['views']}
    overview=load_overview()
    api_values,api_errors=api_overview(items,configs,overview,now)
    errors.update(api_errors)
    if api_values:store_overview(overview,api_values,items,now)
    if api_values or api_errors or any(e.get('api_attempt') for e in overview.values()):save(OVERVIEW,overview)
    if pre is not None:
        observed,browser_errors=pre
        errors.update(browser_errors)
        if observed:
            errors.update(observed.get('errors',{}))
            for url in observed.get('attempted',[]):overview.setdefault(url,{'snapshots':[]})['last_attempt']=now
            for url,m in observed.get('metrics',{}).items():
                if m.get('platform')=='youtube' or 'overview_observed_at' in m:
                    # The Data API owns counters; the browser adds only what the API cannot show.
                    store_overview(overview,{url:m},items,now)
                else:metrics[url]=m
            save(OVERVIEW,overview)
        # Platforms that waited for their turn (or failed) keep their last reading, marked stale.
        carry_over(previous.get('items',[]),metrics,now,only=tuple(PLATFORM_INTERVAL_HOURS))
    else:
        # Keep the last browser-only readings (VK, Dzen, Reddit, ...) visible but marked old.
        carry_over(previous.get('items',[]),metrics,now)
    for url in list(metrics):
        extra=latest_overview(overview,url)
        if extra:metrics[url]={**metrics[url],**extra}
    for item in items:
        item['metrics']=metrics.get(item['url'],{})
        if item['metrics'].get('published_at'):item['published_at']=item['metrics']['published_at']
        elif not item.get('published_at') and item['metrics'].get('published_at_iso'):
            item['published_at']=datetime.fromisoformat(item['metrics']['published_at_iso'].replace('Z','+00:00')).timestamp()
        measured_at=item['metrics'].get('observed_at',now)
        item['age_hours']=(measured_at-item['published_at'])/3600 if item.get('published_at') else None
    try:
        import yt_stats
        yt_stats.append_series(SERIES,series,now)
    except Exception as exc:errors['series']=type(exc).__name__
    snapshot={'observed_at':now,'items':items,'errors':errors,'channels':channels}
    save(folder/(time.strftime('%Y%m%d_%H%M%S')+'.json'),snapshot);save(folder/'latest.json',snapshot)
    for old in sorted(folder.glob('2026*.json'))[:-96]:old.unlink(missing_ok=True)
    # Persist the first available measurement in each fixed age window so a
    # growing lifetime view count cannot overwrite its earlier comparison.
    try:
        import growth_diagnosis
        growth_diagnosis.run(now=now,latest=snapshot,overview=overview,configs=configs)
    except Exception as exc:errors['diagnosis']=type(exc).__name__+': '+str(exc)[:160]
    cohorts=DATA/'content_age_cohorts.json';history=json.loads(cohorts.read_text()) if cohorts.exists() else {}
    record_cohorts(history,items)
    save(cohorts,history)
    brands=analyze(list(history.values()),items)
    feedback={'updated_at':now,'brands':brands,'channels':channels,
        'coverage':{p:sum(1 for x in items if x['platform']==p and any(x.get('metrics',{}).get(k) is not None for k in ('views','impressions','read_completions','score','comments'))) for p in ('telegram','youtube','vk','dzen','reddit','instagram','tiktok')},'errors':errors}
    save(DATA/'content_feedback.json',feedback)
    save(folder/'feedback.json',feedback)
    try:
        import growth_engine
        growth_engine.run(now=now,latest=snapshot,configs=configs)
    except Exception as exc:print('growth engine:',type(exc).__name__,str(exc)[:200],flush=True)
    print(json.dumps(feedback,ensure_ascii=False))


def format_report():
    path=DATA/'content_feedback.json'
    if not path.exists():return 'Замеры контента ещё не собраны. Выводы об аудитории пока недоступны.'
    data=json.loads(path.read_text());stamp=datetime.fromtimestamp(data['updated_at'],timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Europe/Moscow')).strftime('%d.%m %H:%M МСК')
    lines=['<b>Аналитика контента</b>',stamp]
    for platform,count in data['coverage'].items():lines.append(html.escape(platform)+': публикаций с доступными показателями — '+str(count))
    for brand,info in data['brands'].items():
        lines+=['',html.escape(brand),html.escape(info['guidance'])]
        for group in info['same_age_comparisons']:
            lines.append(html.escape(group['platform']+' / '+group['kind'])+': '+str(group['count'])+' замеров в возрасте от суток до тридцати часов')
            for item in group['top']:lines.append('• '+html.escape(item['title'] or '')+' — '+str(item['views'])+' просмотров')
        for voice in info.get('voice_pilot',[]):
            lines.append('Голос '+html.escape(voice['voice'])+': роликов с суточным замером '+str(voice['videos_24h'])+'; медиана удержания '+str(voice['median_percentage_viewed']))
    if data.get('errors'):lines+=['','Часть замеров недоступна: '+html.escape(', '.join(data['errors']))+'. Пропущенные данные не считаются нулевыми просмотрами.']
    return '\n'.join(lines)


if __name__=='__main__':
    import sys
    tick(browser='browser' in sys.argv[1:])
