"""Dispose orphan read-only factory tabs; preserve login, upload and Flow work.

Called under the factory's global browser lease, both before a new browser job
and by the health worker. Browser cookies live in the persistent profile, not
in the discarded dashboards. An owner's visible desktop tab is protected.
"""
import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import build_opener,ProxyHandler

PARK='https://myaccount.google.com/'


def owner_connected():
    for name in ('/proc/net/tcp','/proc/net/tcp6'):
        try:rows=open(name).read().splitlines()[1:]
        except OSError:continue
        for row in rows:
            columns=row.split()
            if len(columns)>3 and columns[1].endswith(':170C') and columns[3]=='01':return True
    return False


def pending_studio():
    for path in Path('/data/output/studio_uploads').glob('*.json'):
        try:receipt=json.loads(path.read_text())
        except (OSError,ValueError):continue
        status=receipt.get('status')
        if status in ('uploading','uploaded_private','awaiting_checks','publishing','posted_unverified'):return True
        if receipt.get('id') and status not in ('published','new','rejected_by_platform','abandoned_unverified'):return True
    return False


def pending_sites():
    sites=set()
    statuses={'uploading','publishing','posted_unverified','share_failed_visible','sending'}
    for platform in ('instagram','tiktok','vk'):
        for path in Path('/data/output').glob('native_'+platform+'_*.json'):
            try:receipt=json.loads(path.read_text())
            except (OSError,ValueError):continue
            if receipt.get('status') in statuses:
                sites.add(platform+'.com')
                if platform=='vk':sites.add('vk.ru')
                break
    return sites


def completed_editor_urls(root=None, open_urls=None):
    """Only saved, hash-matching results release their expensive editor tabs.

    Provider projects remain in Drive/Flow, and all receipts remain on disk.
    A missing or modified download never authorizes closing its editor.
    """
    import hashlib
    root=Path('/data/output') if root is None else Path(root)
    candidates=[]
    for pattern in ('flow_*.json','vids_*.json','post_images/**/pics_receipt.json'):
        candidates.extend(root.glob(pattern))
    wanted=None if open_urls is None else {u.split("?")[0].rstrip("/") for u in open_urls}
    completed=set();pending=set()
    for path in candidates:
        try:receipt=json.loads(path.read_text())
        except (OSError,ValueError):continue
        urls={receipt.get(k,'').split('?')[0].rstrip('/') for k in ('document_url','doc_url','project_url') if isinstance(receipt.get(k),str)}
        urls={u for u in urls if urlsplit(u).hostname in ('docs.google.com','flow.google.com')}
        if wanted is not None:urls &= wanted
        if not urls:continue
        if receipt.get('status') in ('empty_document_after_submission','result_lost_before_confirmation'):
            # Empty editors have no pending artifact. Their original project,
            # prompt and the evidence authorising recovery remain persisted.
            evidence=path.parent/('empty_recovery_ui.json' if receipt['status']=='empty_document_after_submission' else 'last_ui.json')
            try:
                observed=json.loads(evidence.read_text())
                snapshot=observed.get('snapshot','')
                empty='heading "Drop or select an image to start editing"' in snapshot
                proof=(receipt.get('empty_evidence_sha256')==hashlib.sha256(snapshot.encode()).hexdigest()
                    if receipt['status']=='empty_document_after_submission' else
                    (receipt.get('result_loss_evidence') or {}).get('empty_snapshot_sha256')==hashlib.sha256(evidence.read_bytes()).hexdigest())
                if (empty and proof and 'radiogroup "Image previews"' not in snapshot
                        and observed.get('url','').split('?')[0] in urls
                        and (receipt.get('empty_observed_at') or receipt.get('result_loss_observed_at'))):
                    completed.update(urls);continue
            except (OSError,ValueError):pass
        if receipt.get('status')!='completed':pending.update(urls);continue
        file=receipt.get('video_file') or receipt.get('file')
        if not file or not receipt.get('sha256'):continue
        source=Path(file)
        if not source.is_absolute():source=root/source
        try:
            if not source.resolve().is_relative_to(root.resolve()) or not source.is_file():continue
            digest=hashlib.sha256()
            with source.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            if digest.hexdigest()==receipt['sha256']:completed.update(urls)
        except OSError:continue
    return completed-pending


def disposable(url, protect_studio=False,protected_sites=(),completed_editors=()):
    if url.split('?')[0].rstrip('/') in completed_editors:return True
    u=urlsplit(url);host=(u.hostname or '').lower();path=u.path
    if host.removeprefix('www.') in protected_sites:return False
    if host in ('instagram.com','www.instagram.com','tiktok.com','www.tiktok.com'):
        return not any(s in path for s in ('/accounts/','/login','/challenge','/checkpoint','/auth/','/upload'))
    if host in ('youtube.com','www.youtube.com'):
        return path in ('/','/account','/account_advanced','/results','/watch') or path.startswith(('/@','/channel/','/shorts/','/feed/'))
    if host=='studio.youtube.com':
        return not protect_studio and path.startswith('/channel/') and '/video/' not in path and '/upload' not in path
    if host in ('dzen.ru','www.dzen.ru'):return path in ('','/')
    if host in ('vk.com','vk.ru','www.vk.com','www.vk.ru'):
        return bool(re.fullmatch(r'/(?:club|public)\d+/?',path)) or path in ('','/')
    return False


def http_tidy(fetch=None, owner=None):
    """Reclaim disposable targets before Playwright attaches to every renderer.

    Chrome's loopback HTTP target API stays responsive during renderer memory
    stalls. No script execution, cookies, reloads or security settings here.
    With a desktop client attached this fallback changes nothing.
    """
    def report(value):
        value.setdefault('checked_at',time.time())
        try:
            Path('/data/output/browser_cleanup_bootstrap.json').write_text(json.dumps(value))
            if value.get('closed_count'):Path('/data/output/browser_cleanup_nonempty.json').write_text(json.dumps(value))
        except OSError:pass
        return value
    if (owner_connected() if owner is None else owner):return report({'status':'owner_connected','closed_count':0})
    deadline=time.monotonic()+10
    if fetch is None:
        opener=build_opener(ProxyHandler({}))
        def fetch(path):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('cleanup time budget exhausted')
            with opener.open('http://127.0.0.1:9222'+path,timeout=min(2,remaining)) as response:return response.read().decode()
    try:before=json.loads(fetch('/json/list'))
    except Exception as exc:return report({'status':'unavailable','closed_count':0,'error':type(exc).__name__})
    pages=[p for p in before if p.get('type')=='page']
    protect=pending_studio()
    protected_sites=pending_sites()
    completed_editors=completed_editor_urls(open_urls=[p.get("url","") for p in pages])
    candidates=[p for p in pages if disposable(p.get('url',''),protect,protected_sites,completed_editors)]
    # Leave the final window alive even when it contains only old dashboards.
    if len(candidates)==len(pages) and candidates:candidates=candidates[1:]
    requested=[]
    for page in candidates[:20]:
        if time.monotonic()>=deadline-2:break
        identity=page.get('id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',identity):continue
        try:fetch('/json/close/'+identity);requested.append(page)
        except Exception:pass
    try:
        after=json.loads(fetch('/json/list'))
        # /json/close acknowledges before Chrome destroys the target. A single
        # immediate enumeration can still contain it; count only disappearance.
        settle=min(deadline,time.monotonic()+2)
        requested_ids={p['id'] for p in requested}
        while requested_ids & {p.get('id') for p in after} and time.monotonic()<settle:
            time.sleep(.1)
            after=json.loads(fetch('/json/list'))
    except Exception as exc:return report({'status':'unverified','closed_count':0,
        'close_requests':len(requested),'error':type(exc).__name__})
    remaining={p.get('id') for p in after}
    closed=[p for p in requested if p['id'] not in remaining]
    value={'status':'completed','checked_at':time.time(),'before_pages':len(pages),
        'after_pages':sum(p.get('type')=='page' for p in after),'closed_count':len(closed),
        'closed':[p['url'].split('?')[0] for p in closed],
        'retained':[p.get('url','').split('?')[0] for p in after if p.get('type')=='page'],
        'pending_studio_uploads_protected':protect,'source':'Chrome loopback HTTP target API'}
    return report(value)


async def tidy(context, owner=None):
    owner=owner_connected() if owner is None else owner
    pages=list(context.pages);protected_studio=pending_studio()
    protected_sites=pending_sites()
    completed_editors=await asyncio.to_thread(completed_editor_urls,open_urls=[p.url for p in pages])
    accounts=[p for p in pages if urlsplit(p.url).hostname=='myaccount.google.com']
    if accounts and not owner:
        try:await asyncio.wait_for(accounts[0].bring_to_front(),2)
        except Exception:pass
    spare=accounts[1:];seen=set();closed=[];errors=[];touched=0
    projects=[p for p in pages if urlsplit(p.url).hostname=='flow.google.com' and '/project/' in urlsplit(p.url).path and '/edit/' not in urlsplit(p.url).path]
    for page in pages:
        u=urlsplit(page.url)
        if disposable(page.url,protected_studio,protected_sites,completed_editors):spare.append(page)
        if u.hostname=='flow.google.com' and '/project/' in u.path:
            if '/edit/' in u.path:
                # Only the same project's root can replace its retained editor.
                if any(urlsplit(p.url).path==u.path.split('/edit/')[0] for p in projects):spare.append(page)
            elif (u.hostname,u.path) in seen:spare.append(page)
            else:seen.add((u.hostname,u.path))
    spare=list(dict.fromkeys(spare))
    if owner:
        for page in list(spare):
            try:visible=await asyncio.wait_for(page.evaluate('document.visibilityState === "visible"'),2)
            except Exception:visible=True
            if visible:spare.remove(page)
    if len(spare)==len(pages) and pages:
        spare.remove(pages[0])
        await asyncio.wait_for(pages[0].goto(PARK,wait_until='commit'),10);touched+=1
    for page in pages:
        u=urlsplit(page.url)
        try:
            if page in spare:
                url=page.url.split('?')[0]
                await asyncio.wait_for(page.close(),3);closed.append(url);touched+=1
            elif not owner and u.hostname=='flow.google.com' and '/project/' not in u.path:
                await asyncio.wait_for(page.goto(PARK,wait_until='commit'),10);touched+=1
        except Exception as exc:errors.append(type(exc).__name__)
    report={'checked_at':time.time(),'closed_count':len(closed),'closed':closed,'touched_count':touched,
        'retained':[p.url.split('?')[0] for p in context.pages],'owner_connected':owner,
        'pending_studio_uploads_protected':protected_studio,'errors':errors}
    try:Path('/data/output/browser_cleanup_last.json').write_text(json.dumps(report))
    except OSError:pass
    return report
