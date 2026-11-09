"""Read native owner-visible counters and visibility without publishing anything."""
import asyncio,fcntl,json,re,sys,time
from pathlib import Path
from playwright.async_api import async_playwright,expect
from browser_session import connect
from youtube_studio_uploader import ensure_channel
from metrics_numbers import parse_count,dzen_dashboard,match_tiles
import studio_overview
import host_load

ROOT=Path('/data/output')
MAX_OVERVIEWS=4  # Studio pages read per brand per run; each takes up to ~25 s
ORDER=('vk','dzen','tiktok','instagram')
# The readers below use text and DOM attributes only. Decoding feed images and autoplaying video was the bulk
# of each tab's memory (renderers of 270-500 MB measured on 2026-11-01). URL patterns, not Playwright routing:
# routing switches the HTTP cache off, which would re-download every script bundle on each run.
BLOCKED_URLS=['*.jpg*','*.jpeg*','*.png*','*.webp*','*.gif*','*.avif*','*.mp4*','*.webm*','*.m4s*','*.m3u8*','*.woff*','*.ttf*','*.otf*']


async def lighten(page):
    try:
        session=await page.context.new_cdp_session(page)
        await session.send('Network.enable')
        await session.send('Network.setBlockedURLs',{'urls':BLOCKED_URLS})
    except Exception:pass  # a heavier page is better than no reading



def percentage(body):
    m=re.search(r'(?:Average percentage viewed|Средний процент просмотра)\s*[:\n]?\s*([\d.,]+)\s*%',body,re.I)
    return float(m[1].replace(',','.')) if m else None


async def youtube(page,cfg,items,metrics,raw,targets=()):
    """Per-video Studio overview: the numbers the Data API cannot give (stayed to watch, view duration,
    subscribers gained, traffic sources). Counters, privacy and comments come from yt_stats instead."""
    state={'channel_id':cfg['youtube_channel_id'],'channel_title':cfg['youtube_channel_title'],'author_url':cfg['youtube_author_url']}
    await ensure_channel(page,state)
    mine={x['url'] for x in items}
    for url in [u for u in targets if u in mine][:MAX_OVERVIEWS]:
        video=url.rsplit('/',1)[-1];parsed={}
        try:
            # hl=en: the overview labels are parsed in English whatever language the session picked.
            await page.goto('https://studio.youtube.com/video/'+video+'/analytics/tab-overview/period-default?hl=en',
                wait_until='domcontentloaded',timeout=30000)
            for _ in range(16):
                await page.wait_for_timeout(1500)
                body=await page.locator('body').inner_text();parsed=studio_overview.parse(body)
                if studio_overview.complete(parsed):break
        except Exception as exc:
            raw['youtube_overview_'+video]={'status':type(exc).__name__};continue
        raw['youtube_overview_'+video]={'status':'ok' if studio_overview.complete(parsed) else 'not exposed yet','text':body[-1500:]}
        if studio_overview.complete(parsed):
            metrics.setdefault(url,{}).update(parsed,overview_observed_at=time.time(),
                retention_source='Studio individual video analytics overview')


async def vk(page,items,metrics,raw):
    await page.goto('https://vk.ru/club242642200',wait_until='domcontentloaded',timeout=45000)
    await page.get_by_role('heading',name='ThinkScope',exact=True,level=1).wait_for(timeout=30000)
    await page.get_by_test_id('post').first.wait_for(timeout=30000)
    for _ in range(12):await page.mouse.wheel(0,1100);await asyncio.sleep(0.6)
    rows=await page.get_by_test_id('post_text').evaluate_all('''es=>es.map(n=>{
      let root=n;
      for(let p=n.parentElement;p && p!==document.body;p=p.parentElement){
        if(p.querySelectorAll('[data-testid="post_text"]').length!==1)break;
        root=p;
      }
      return {id:n.getAttribute('data-post-id'),text:root.innerText.slice(-4000),
        labels:[...root.querySelectorAll('[aria-label],[title],[data-testid]')].map(e=>({label:e.getAttribute('aria-label'),title:e.getAttribute('title'),testid:e.getAttribute('data-testid'),text:e.innerText?.slice(0,100)})).slice(-60)};
    })''')
    raw['vk_posts']=rows
    for row in rows:
        if not re.fullmatch(r'-242642200_\d+',row.get('id') or ''):continue
        url='https://vk.ru/wall'+row['id'];labels=' '.join((v.get('label') or '')+' '+(v.get('title') or '') for v in row['labels'])
        views=re.search(r'([\d.,\s]+)\s*просмотр',labels,re.I)
        values={}
        if views:values['views']=parse_count(views[1])
        for name,pattern in [('comments',r'([\d.,\s]+)\s*комментари'),('shares',r'([\d.,\s]+)\s*Поделиться')]:
            match=re.search(pattern,labels,re.I)
            if match:values[name]=parse_count(match[1])
        if values:metrics[url]={**values,'observed_at':time.time(),'source':'VK owning public post labelled counters','window':'since publication'}


async def dzen(page,items,metrics,raw):
    channel='6a6f8db6fad4763cdb24fba1'
    await page.goto('https://dzen.ru/profile/editor/id/'+channel,wait_until='domcontentloaded',timeout=45000)
    await page.get_by_role('link',name='Смыслоскоп',exact=True).wait_for(timeout=30000)
    await asyncio.sleep(1)
    body=await page.locator('body').inner_text();raw['dzen_studio']={'text':body[-15000:],
        'links':await page.locator('a[href]').evaluate_all('(es)=>es.filter(e=>/statistics|stats|edit/.test(e.href)).map(e=>({text:e.innerText,url:e.href})).slice(-40)')}
    for url,values in dzen_dashboard(body,items).items():
        metrics[url]={**values,'observed_at':time.time(),'source':'Dzen owner Studio dashboard card',
            'window':'Dashboard card; time window not stated'}
    if any(x['platform']=='dzen' for x in items) and not any(x['url'] in metrics for x in items if x['platform']=='dzen'):
        raise RuntimeError('Dzen dashboard did not expose labelled counters for the published articles')


async def reddit(page,items,metrics,raw):
    for item in [x for x in items if x['platform']=='reddit'][:5]:
        identity=re.search(r'/comments/([a-z0-9]+)/',item['url'])
        if not identity:continue
        await page.goto(item['url'],wait_until='domcontentloaded',timeout=25000)
        node=page.locator('shreddit-post[id="t3_'+identity[1]+'"]')
        await node.wait_for(timeout=15000)
        attrs=await node.evaluate('(e)=>Object.fromEntries([...e.attributes].map(a=>[a.name,a.value]))')
        if attrs.get('author','').casefold()!='thinkscope':raise RuntimeError('Reddit metrics belong to another profile')
        raw['reddit_'+identity[1]]={k:attrs.get(k) for k in ('author','post-title','score','comment-count','view-count')}
        values={name:parse_count(attrs.get(attribute,'')) for name,attribute in [('score','score'),('comments','comment-count'),('views','view-count')]}
        if all(v is None for v in values.values()):raise RuntimeError('Reddit post counters are not exposed')
        metrics[item['url']]={**values,'observed_at':time.time(),'source':'Own Reddit post labelled DOM counters','window':'since publication'}


ACCOUNTS={}  # platform -> usernames from the brand configuration, set by collect()


async def tiktok(page,items,metrics,raw):
    """Own TikTok profile grid: every tile shows its play count to any visitor."""
    if not any(x['platform']=='tiktok' for x in items):return
    for user in sorted(ACCOUNTS.get('tiktok',()))[:2]:
        await page.goto('https://www.tiktok.com/@'+user,wait_until='domcontentloaded',timeout=45000)
        tile=page.locator('[data-e2e="user-post-item"]');await tile.first.wait_for(timeout=30000)
        # The tiles exist before their counters are drawn (measured 2026-11-01: every count was '' right after the
        # tile appeared and '0' six seconds later), so an unfilled grid used to read as 'no data'.
        try:await page.wait_for_function('''()=>[...document.querySelectorAll('[data-e2e="user-post-item"] [data-e2e="video-views"]')].some(e=>e.innerText.trim())''',timeout=15000)
        except Exception:pass
        rows=await tile.evaluate_all("""es=>es.map(e=>({href:(e.querySelector('a[href*="/video/"]')||{}).href||'',
            text:((e.querySelector('[data-e2e="video-views"]')||{}).innerText)||''}))""")
        raw['tiktok_'+user]=rows[:40]
        metrics.update(match_tiles('tiktok',items,rows,time.time()))


async def instagram(page,items,metrics,raw):
    """Own Instagram Reels grid (signed-in session): each tile shows its play count."""
    if not any(x['platform']=='instagram' for x in items):return
    for user in sorted(ACCOUNTS.get('instagram',()))[:2]:
        await page.goto('https://www.instagram.com/'+user+'/reels/',wait_until='domcontentloaded',timeout=45000)
        tile=page.locator('a[href*="/reel/"]');await tile.first.wait_for(timeout=30000)
        for _ in range(3):await page.mouse.wheel(0,900);await asyncio.sleep(0.6)
        rows=await tile.evaluate_all("es=>es.map(e=>({href:e.href,text:e.innerText}))")
        raw['instagram_'+user]=rows[:40]
        metrics.update(match_tiles('instagram',items,rows,time.time()))


async def collect(path):
    job=json.loads(Path(path).read_text());metrics={};raw={};errors={};ran=[];skipped=[]
    for cfg in job['brands'].values():
        for platform in ('tiktok','instagram'):
            target=((cfg.get('publishing') or {}).get(platform) or {}).get('target')
            if target:ACCOUNTS.setdefault(platform,set()).add(target.lstrip('@'))
    started=time.time()
    out=ROOT/'content_analytics/browser_latest.json';out.parent.mkdir(exist_ok=True)
    def persist(phase):
        out.write_text(json.dumps({'metrics':metrics,'errors':errors,'ran':ran,'skipped':skipped,'raw':raw,'observed_at':time.time(),
            'started_at':started,'phase':phase},ensure_ascii=False,indent=2));out.chmod(0o644)
    async def close_page(page,name):
        try:await asyncio.wait_for(page.close(),timeout=5)
        except Exception as exc:errors[name+'/cleanup']=type(exc).__name__
    def calm(label):
        """Optional reading: leave it for the next pass when the host is short of memory or disk bandwidth."""
        reason=None if job.get('force') else host_load.busy()
        if reason:skipped.append(label+': '+reason)
        return reason is None
    if not job.get('overview_targets') and not any(n in ORDER for n in job.get('platforms',ORDER)):
        persist('completed')
        return {'metrics':metrics,'errors':errors,'ran':ran,'skipped':skipped}
    with (ROOT/'.flow-generation.lock').open('a') as lock:
        # Generation can hold the one browser lease for an hour. Wait for a gap instead of failing at once:
        # 101 of 120 runs on 2026-10-31/11-01 died on an immediate non-blocking attempt.
        deadline=time.time()+job.get('lease_wait',420)
        while True:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.time()>=deadline:raise TimeoutError('browser lease stayed busy for '+str(job.get('lease_wait',420))+' s')
                await asyncio.sleep(3)
        async with async_playwright() as p:
            b=await connect(p)
            try:
                targets=job.get('overview_targets',[])
                for brand,cfg in job['brands'].items():
                    if not any(x['brand']==brand and x['url'] in targets for x in job['items']):continue
                    if not calm(brand+'/youtube'):continue
                    page=await b.contexts[0].new_page();await lighten(page)
                    page.set_default_timeout(15000);persist(brand+'/youtube/reading')
                    try:await asyncio.wait_for(youtube(page,{**cfg,'brand':brand},[x for x in job['items'] if x['brand']==brand],metrics,raw,targets),timeout=60+MAX_OVERVIEWS*30)
                    except Exception as exc:errors[brand+'/youtube']=type(exc).__name__+': '+str(exc)[-250:]
                    finally:await close_page(page,brand+'/youtube')
                    persist(brand+'/youtube')
                readers={'vk':(vk,45),'dzen':(dzen,35),'tiktok':(tiktok,60),'instagram':(instagram,60)}
                for name in [n for n in ORDER if n in job.get('platforms',ORDER)]:
                    fn,budget=readers[name]
                    if not calm(name):continue
                    page=await b.contexts[0].new_page();await lighten(page)
                    page.set_default_timeout(15000);persist(name+'/reading')
                    try:await asyncio.wait_for(fn(page,job['items'],metrics,raw),timeout=budget)
                    except Exception as exc:errors[name]=type(exc).__name__+': '+str(exc)[-250:]
                    finally:await close_page(page,name)
                    ran.append(name)
                    persist(name)
                persist('completed')
                return {'metrics':metrics,'errors':errors,'ran':ran,'skipped':skipped}
            finally:await b.close()


if __name__=='__main__':print(json.dumps(asyncio.run(collect(sys.argv[1])),ensure_ascii=False))
