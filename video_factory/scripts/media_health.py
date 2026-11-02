"""Report freshness and stuck items, not merely whether timers are running."""
import fcntl
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
import requests
from script_generator import BASE_DIR
from youtube_uploader import save


BUSY_RESTART_FAILURES=6


def host_busy():
    import host_load
    return host_load.busy()


def browser_ready():
    from browser_process import run
    # The debugging HTTP endpoint can answer while attaching to its pages hangs.
    # Exercise the same browser path as publishers, using only a blank test tab.
    code='''import asyncio
from playwright.async_api import async_playwright
async def main():
 async with async_playwright() as p:
  b=await p.chromium.connect_over_cdp('http://127.0.0.1:9222',timeout=10000)
  page=None
  try:
   page=await asyncio.wait_for(b.contexts[0].new_page(),5)
   assert await asyncio.wait_for(page.title(),3)==''
  finally:
   if page:await asyncio.wait_for(page.close(),3)
   await asyncio.wait_for(b.close(),3)
asyncio.run(main())'''
    try:
        result=run(['docker','exec','video_factory_auth','python3','-c',code],
            capture_output=True,text=True,timeout=60)
        return result.returncode==0
    except subprocess.TimeoutExpired:return False


PARK_URL='https://myaccount.google.com/'


def park_idle_browser():
    """Park Flow's landing tab on a static Google page and drop spare duplicate tabs; returns how many were touched.

    Flow's start page animates without end. Measured while nothing ran: 92% for its renderer
    and 79% for the software GPU process, of this host's two cores, starving every other
    service. myaccount.google.com keeps the same signed-in session at under 9% in total.
    Call it only under the global generation lease. A /project/ page may hold a generation
    in flight, so only non-project Flow pages are navigated away.

    Instagram and TikTok pages are parked too: a publisher leaves its tab on the autoplaying
    feed, and measured on 2026-11-01 that one software-rendered renderer used 69% of a core
    (74 CPU minutes) for hours while nothing was posting. Publishers navigate on their own.

    Spare tabs are closed: every Flow request that stays retained leaves its project tab, and the editor
    tab it ended on is never reused (the next job looks for the /project/ path), so these accumulate;
    each costs a 270-400 MB renderer on a 4.9 GB host. A /project/ page is always kept, and so is one
    myaccount tab, so the browser never loses its last window.

    Nothing is touched while someone is connected to the desktop (noVNC): the owner logging in to a
    new YouTube or Instagram account must not have the page moved under their hands."""
    from browser_process import run
    code='''import asyncio
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
SOCIAL=('instagram.com','tiktok.com')
def owner_connected():
 for name in ('/proc/net/tcp','/proc/net/tcp6'):
  try:rows=open(name).read().splitlines()[1:]
  except OSError:continue
  for row in rows:
   cols=row.split()
   if len(cols)>3 and cols[1].endswith(':170C') and cols[3]=='01':return True
 return False
async def main():
 if owner_connected():print(0);return
 async with async_playwright() as p:
  b=await p.chromium.connect_over_cdp('http://127.0.0.1:9222',timeout=10000)
  parked=0
  try:
   pages=list(b.contexts[0].pages)
   for page in pages:
    if page.url.startswith('https://myaccount.google.com'):
     try:await asyncio.wait_for(page.bring_to_front(),5)
     except Exception:pass
     break
   spare=[]
   if any(urlsplit(x.url).netloc=='flow.google.com' and '/project/' in urlsplit(x.url).path and '/edit/' not in urlsplit(x.url).path for x in pages):
    spare+=[x for x in pages if urlsplit(x.url).netloc=='flow.google.com' and '/edit/' in urlsplit(x.url).path]
   accounts=[x for x in pages if urlsplit(x.url).netloc=='myaccount.google.com']
   spare+=accounts[1:]
   for page in pages:
    u=urlsplit(page.url)
    if page in spare:
     await asyncio.wait_for(page.close(),5);parked+=1
    elif (u.netloc=='flow.google.com' and '/project/' not in u.path) or u.netloc.endswith(SOCIAL):
     await asyncio.wait_for(page.goto('@PARK@',wait_until='commit'),20);parked+=1
  finally:
   await asyncio.wait_for(b.close(),3)
  print(parked)
asyncio.run(main())'''.replace('@PARK@',PARK_URL)
    try:
        result=run(['docker','exec','video_factory_auth','python3','-c',code],capture_output=True,text=True,timeout=60)
        return int(result.stdout.strip().splitlines()[-1]) if result.returncode==0 and result.stdout.strip() else 0
    except (subprocess.TimeoutExpired,ValueError):return 0


def unfinished_studio_uploads():
    return [p.name for p in (BASE_DIR/'output/studio_uploads').glob('*.json')
        if (d:=json.loads(p.read_text())).get('id') and d.get('status') not in ('published','new')]


def tick():
    now=time.time();problems=[]
    from media_network import check as network_check
    try:
        network=network_check()
        if network['status']=='unavailable':problems.append({'issue':'media_network_unavailable','recovery':network.get('recovery','no working configured media route')})
    except Exception as exc:
        network={'status':'check_failed','error':type(exc).__name__};problems.append({'issue':'media_network_check_failed'})
    free=shutil.disk_usage(str(BASE_DIR)).free
    if free<8*2**30:problems.append({'issue':'disk_low','free_gb':round(free/2**30,1)})
    cfg=json.loads((BASE_DIR/'data/media_brands.json').read_text())
    schedule=json.loads((BASE_DIR/'data/media_schedule.json').read_text())
    for brand,settings in cfg.items():
        for platform,target in settings.get('publishing',{}).items():
            if target.get('moderation_hold'):
                problems.append({'brand':brand,'platform':platform,'issue':'platform_moderation_hold',
                    'reason':target['moderation_hold'].get('reason')})
    for brand,settings in cfg.items():
        if not settings.get('video_enabled'):continue
        slot=schedule.get(brand,{})
        if now>slot.get('next_due',now)+1800:
            problems.append({'brand':brand,'issue':'publication_overdue','seconds':int(now-slot['next_due']),
                'job':slot.get('job'),'error':slot.get('error')})
    for unit in ('tg-autoposter.service','video-autoposter.timer','media-research.timer','media-distribution.timer','media-images.timer','media-analytics.timer'):
        active=subprocess.run(['systemctl','--user','is-active',unit],capture_output=True,text=True).stdout.strip()
        if active!='active':
            subprocess.run(['systemctl','--user','start',unit],check=False)
            problems.append({'unit':unit,'issue':'unit_was_inactive','recovery':'start requested'})
    feedback=BASE_DIR/'data/content_feedback.json'
    if not feedback.exists() or now-feedback.stat().st_mtime>7200:
        problems.append({'issue':'analytics_stale'})
    elif json.loads(feedback.read_text()).get('errors'):
        problems.append({'issue':'analytics_incomplete','targets':list(json.loads(feedback.read_text())['errors'])})
    research=BASE_DIR/'data/research_status.json'
    if research.exists():
        statuses=json.loads(research.read_text())
        stale=[name for name,value in statuses.items() if now-value.get('checked_at',0)>2700]
        if stale:problems.append({'issue':'research_tasks_stale','tasks':stale})
        failed=[name for name,value in statuses.items() if value.get('status')!='completed']
        if failed:problems.append({'issue':'research_tasks_retrying','tasks':failed})
    distribution=BASE_DIR/'data/distribution_status.json'
    if distribution.exists():
        statuses=json.loads(distribution.read_text())
        stale=[name for name,value in statuses.items() if now-value.get('checked_at',0)>3600]
        if stale:problems.append({'issue':'distribution_tasks_stale','tasks':stale})
    holds=BASE_DIR/'data/distribution/target_holds.json'
    if holds.exists():
        for key,value in json.loads(holds.read_text()).items():
            if value.get('until',0)>now and value.get('fails',0)>=3:
                problems.append({'issue':'platform_account_failing','target':key,'fails':value['fails'],
                    'needs_login':value.get('needs_login'),'reason':str(value.get('reason'))[-200:]})
    for path in (BASE_DIR/'output').glob('native_vk_photo_*.json'):
        if not re.fullmatch(r'native_vk_photo_[a-f0-9]{64}\.json',path.name):continue
        value=json.loads(path.read_text())
        if value.get('status')=='publishing' and now-value.get('publish_requested_at',now)>1800:
            problems.append({'issue':'photo_publication_unverified','source_id':value['source_id']})
    for platform in ('instagram','tiktok'):
        for path in (BASE_DIR/'output').glob('native_'+platform+'_*.json'):
            if not re.fullmatch(r'native_'+platform+r'_[a-f0-9]{64}\.json',path.name):continue
            value=json.loads(path.read_text())
            if (value.get('status') in ('publishing','posted_unverified','share_failed_visible')
                    and now-value.get('share_attempted_at',now)>1800):
                problems.append({'issue':platform+'_publication_unverified','source_id':value.get('source_id'),
                    'reason':value.get('last_error')})
    # Restart a dead browser only with the same global lease used by generators.
    # The persistent profile survives the container restart.
    parked=0
    with (BASE_DIR/'output/.flow-generation.lock').open('r') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            pass  # An active browser job owns readiness; do not interrupt it.
        else:
            readiness=BASE_DIR/'data/browser_readiness.json'
            previous=json.loads(readiness.read_text()) if readiness.exists() else {}
            # Bound the whole health tick when network failover already used time.
            unavailable=False
            if time.time()-now<60:
                ready=browser_ready()
                failures=0 if ready else previous.get('consecutive_failures',0)+1
                # The 5 s attach probe fails whenever the host stalls on memory/disk (measured 2026-11-01: the
                # browser was restarted in the middle of a stall and every open Flow page was lost). Restarting
                # changes nothing about a stall, so while the host is busy a restart waits for 6 failures.
                stalled=None if ready else host_busy()
                save(readiness,{'checked_at':time.time(),'ready':ready,'consecutive_failures':failures,'host_busy':stalled})
                if not ready:
                    problems.append({'issue':'browser_attachment_unavailable','consecutive_failures':failures})
                    unavailable=failures>=(BUSY_RESTART_FAILURES if stalled else 2)
                elif time.time()-now<75:parked=park_idle_browser()
            # We hold the lease, so no upload is running now. A saved upload can wait for a short failure, but a hung Chrome
            # can never finish it (measured 2026-11-02: every publisher failed until a manual restart), and its receipt survives.
            if unavailable and unfinished_studio_uploads() and failures<BUSY_RESTART_FAILURES:
                problems.append({'issue':'browser_unavailable','recovery':'saved Studio upload must be reconciled before restart'})
            elif unavailable:
                try:
                    restarted=subprocess.run(['docker','restart','video_factory_auth'],capture_output=True,timeout=45)
                    recovery='persistent container restarted' if restarted.returncode==0 else 'container restart failed'
                except subprocess.TimeoutExpired:recovery='container restart timed out; recheck next tick'
                problems.append({'issue':'browser_unavailable','recovery':recovery})
    try:
        import owner_notify
        owner_notify.overdue(problems)
        owner_notify.problems(problems)
        owner_notify.flush_events()
    except Exception:pass
    report={'checked_at':now,'status':'degraded' if problems else 'fresh','problems':problems,'media_network':network,'browser_parked':parked,
        'paused_targets':{b:[p+('_posts' if p=='reddit' and v.get('enabled') and not v.get('post_enabled') else '')
            for p,v in x.get('publishing',{}).items() if not v.get('enabled') or (p=='reddit' and not v.get('post_enabled'))] for b,x in cfg.items()}}
    save(BASE_DIR/'data/media_health.json',report)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':tick()
