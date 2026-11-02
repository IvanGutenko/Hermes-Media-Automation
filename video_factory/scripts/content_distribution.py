#!/usr/bin/env python3
"""Persisted per-target publication receipts and native Telegram/VK distribution."""
import fcntl
import hashlib
import html
import json
import os
import re
import subprocess
import time
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from script_generator import BASE_DIR, PROXIES
from youtube_uploader import save
from browser_process import run as run_browser,wait_for_lease,BrowserBusy

DATA=BASE_DIR/'data'
CONFIG=DATA/'media_brands.json'


LEASE_WAIT_SECONDS=900
VIDEO_WINDOW=72*3600     # older clips of news are not worth posting late
POST_WINDOW=72*3600
MAX_BROWSER_PUBLICATIONS=3   # per distribution run, so generation also gets the browser
HOLD_MAX=6*3600
LOGIN_HOLD=3600
TERMINAL_RECEIPTS=('published','rejected_by_platform','abandoned_unverified','duplicate_removed')


def browser_contention(exc):
    """The shared browser was busy or not answering when a helper needed it. That is not the account's fault: no hold, next tick retries."""
    text=str(exc)
    return 'BlockingIOError' in text or 'connect_over_cdp' in text


def run_publisher(command,*,timeout,**kwargs):
    """One browser publisher, waiting its turn for the shared browser; BrowserBusy when the turn never comes."""
    deadline=time.monotonic()+LEASE_WAIT_SECONDS
    while True:
        if not wait_for_lease(max(deadline-time.monotonic(),0),BASE_DIR):
            raise BrowserBusy('The shared browser stayed busy for %d s'%LEASE_WAIT_SECONDS)
        result=run_browser(command,timeout=timeout,**kwargs)
        if result.returncode and 'BlockingIOError' in str(getattr(result,'stderr','') or ''):
            if time.monotonic()>=deadline:raise BrowserBusy('Another worker took the browser first')
            time.sleep(3);continue
        # Chrome not answering says nothing about this account: no hold on it, the next tick simply tries again.
        if result.returncode and 'connect_over_cdp' in str(getattr(result,'stderr','') or ''):
            raise BrowserBusy('The shared browser did not answer')
        return result


def _holds_path():return DATA/'distribution'/'target_holds.json'


def _holds():
    try:return json.loads(_holds_path().read_text())
    except (OSError,ValueError):return {}


def _hold_key(platform,target,kind):return f'{platform}:{target}:{kind}'


def target_held(platform,target,kind):
    """Reason while this platform account is resting after failures; a failing site is not reopened every tick."""
    holds=_holds();now=time.time()
    for key in (_hold_key(platform,target,kind),_hold_key(platform,target,'*')):
        if holds.get(key,{}).get('until',0)>now:return holds[key].get('reason') or 'resting after a failure'
    return None


def note_failure(platform,target,kind,exc):
    """Exponential rest per platform account and media kind. A lost login rests every kind of that account."""
    text=str(exc)
    login=bool(re.search(r'NeedsLogin|not signed in|must log in',text))
    holds=_holds();key=_hold_key(platform,target,'*' if login else kind)
    fails=holds.get(key,{}).get('fails',0)+1
    wait=min(HOLD_MAX,900*2**(fails-1))
    if login:wait=max(wait,LOGIN_HOLD)
    holds[key]={'fails':fails,'until':time.time()+wait,'reason':text[-300:],'needs_login':login,'at':time.time()}
    _holds_path().parent.mkdir(parents=True,exist_ok=True)
    save(_holds_path(),holds)


def note_success(platform,target,kind):
    holds=_holds()
    removed=[holds.pop(k,None) for k in (_hold_key(platform,target,kind),_hold_key(platform,target,'*'))]
    if any(removed):save(_holds_path(),holds)


def publication_key(brand,cfg,source_id,kind):
    return hashlib.sha256(json.dumps({'brand':brand,'target':cfg.get('target',cfg.get('group_id')),'source':source_id,'kind':kind},sort_keys=True).encode()).hexdigest()


def receipt_of(brand,platform,cfg,source_id,kind='video'):
    path=DATA/'distribution'/f'{brand}_{platform}_{publication_key(brand,cfg,source_id,kind)}.json'
    try:return path,json.loads(path.read_text())
    except (OSError,ValueError):return path,None


def _announce(brand,platform,payload,result):
    """Owner roll-up entry for a confirmed publication; reporting can never affect publishing."""
    try:
        import owner_notify
        owner_notify.publication(brand,platform,'video' if payload.get('video') else 'post',payload.get('title'),result.get('url'),payload.get('lang'))
    except Exception:pass


def _alert(brand,platform,exc):
    try:
        import owner_notify
        owner_notify.failure(brand,platform,str(exc))
    except Exception:pass


def update_reddit_cursor(path,source_id,published_at,url):
    previous=json.loads(path.read_text()) if path.exists() else {}
    if previous.get('last_post_at',0)>=published_at:return
    save(path,{'source_id':source_id,'last_post_at':published_at,'url':url})


def sync_reddit_cursor(path,username):
    # The browser can finish publication before the calling worker times out.
    # Its verified durable receipt must also count towards this profile's cadence.
    for receipt in (BASE_DIR/'output').glob('native_reddit_*.json'):
        if not re.fullmatch(r'native_reddit_[a-f0-9]{64}\.json',receipt.name):continue
        proof=json.loads(receipt.read_text())
        if (proof.get('status')=='published' and proof.get('username','').casefold()==username.casefold()
                and proof.get('public_verified_at') and proof.get('url')):
            update_reddit_cursor(path,proof['source_id'],
                proof.get('publication_attempted_at') or proof['public_verified_at'],proof['url'])


def checked(response,service):
    if not response.ok:raise RuntimeError(f'{service} HTTP {response.status_code}')
    body=response.json()
    if body.get('error') or body.get('ok') is False:
        err=body.get('error',{})
        raise RuntimeError(f'{service} rejected operation: '+str(err.get('error_code') if isinstance(err,dict) else body.get('error_code')))
    return body


def telegram(cfg,payload,state,persist):
    token=os.environ.get(cfg['token_env'])
    if not token:raise RuntimeError('Telegram bot token is missing')
    root='https://api.telegram.org/bot'+token+'/'
    def call(method,data,files=None):
        try:return checked(requests.post(root+method,data=data,files=files,proxies=PROXIES,timeout=90),'Telegram')
        except requests.RequestException:raise RuntimeError('Telegram network failure; receipt retained') from None
    chat=call('getChat',{'chat_id':cfg['target']})['result']
    if chat['type']!='channel' or chat.get('username','').lower()!=cfg['target'].lstrip('@').lower():
        raise RuntimeError('Telegram target identity mismatch')
    if state.get('id'):
        return state
    if state.get('status')=='sending':
        # Reconcile a lost Bot API response against the public channel, never blindly resend.
        page=requests.get('https://t.me/s/'+cfg['target'].lstrip('@'),proxies=PROXIES,timeout=30)
        soup=BeautifulSoup(page.text,'html.parser')
        for msg in soup.select('.tgme_widget_message'):
            text=msg.select_one('.tgme_widget_message_text')
            if text and text.get_text('\n',strip=True)==state.get('sent_plain',state.get('sent_text')):
                state.update(id=int(msg['data-post'].split('/')[-1]),status='published',url='https://t.me/'+msg['data-post']);persist();return state
        raise RuntimeError('Telegram send is uncertain; waiting for reconciliation, no duplicate send')
    text=payload['text']
    if payload.get('video'):
        text=payload['title']+'\n\n'+payload.get('source_url','')
        text=text[:1024]
    if len(text)>4096:raise ValueError('Telegram post is too long')
    if payload.get('image') and len(BeautifulSoup(text,'html.parser').get_text())>1024:
        raise ValueError('Photo caption exceeds Telegram limit')
    state.update(status='sending',sent_text=text,sent_plain=BeautifulSoup(text,'html.parser').get_text() if payload.get('parse_mode')=='HTML' else text);persist()
    if payload.get('video'):
        with open(payload['video'],'rb') as stream:
            result=call('sendVideo',{'chat_id':chat['id'],'caption':text,'supports_streaming':'true'},files={'video':('short.mp4',stream,'video/mp4')})['result']
    elif payload.get('image'):
        data={'chat_id':chat['id'],'caption':text}
        if payload.get('parse_mode'):data['parse_mode']=payload['parse_mode']
        with open(payload['image'],'rb') as stream:
            result=call('sendPhoto',data,files={'photo':('image.jpg',stream,'image/jpeg')})['result']
        if not result.get('photo'):raise RuntimeError('Telegram did not confirm the post image')
    else:
        data={'chat_id':chat['id'],'text':text,'link_preview_options':json.dumps({'is_disabled':True})}
        if payload.get('parse_mode'):data['parse_mode']=payload['parse_mode']
        result=call('sendMessage',data)['result']
    state.update(id=result['message_id'],status='published',chat_id=chat['id'],url='https://t.me/'+chat['username']+'/'+str(result['message_id']))
    persist();return state


def vk(cfg,payload,state,persist):
    if payload.get('video'):
        raw=Path(payload['video']).resolve()
        job=BASE_DIR/'output'/('vk_native_job_'+state['key']+'.json')
        save(job,{'group_id':int(cfg['group_id']),'group_title':cfg.get('group_title','ThinkScope'),
            'video':'/data/output/'+str(raw.relative_to((BASE_DIR/'output').resolve())),
            'sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),'title':payload['title'],
            'description':payload['text'],'source_id':payload['source_id']})
        result=run_publisher(['docker','exec','video_factory_auth','python3','-u','/app/scripts/vk_video_publisher.py',
            '/data/output/'+job.name],capture_output=True,text=True,timeout=900)
        if result.returncode:raise RuntimeError('Native VK upload retained for retry: '+result.stderr[-600:])
        state.update(json.loads(result.stdout.strip().splitlines()[-1]));persist();return state
    if payload.get('image'):
        raw=Path(payload['image']).resolve();job=BASE_DIR/'output'/('vk_photo_job_'+state['key']+'.json')
        save(job,{'key':state['key'],'group_id':int(cfg['group_id']),'group_title':cfg.get('group_title','ThinkScope'),
            'image':'/data/output/'+str(raw.relative_to((BASE_DIR/'output').resolve())),
            'sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),'text':payload['text'],'source_id':payload['source_id']})
        if state.get('status')=='new':
            state['status']='preparing';persist()
        try:
            result=run_publisher(['docker','exec','video_factory_auth','python3','-u','/app/scripts/vk_photo_publisher.py',
                '/data/output/'+job.name],capture_output=True,text=True,timeout=360)
        finally:
            native=BASE_DIR/'output'/('native_vk_photo_'+state['key']+'.json')
            if native.exists():
                proof=json.loads(native.read_text())
                if (proof.get('source_id')==payload['source_id'] and proof.get('sha256')==hashlib.sha256(raw.read_bytes()).hexdigest()
                        and proof.get('text')==payload['text'] and proof.get('status') in ('preparing','publishing','published')):
                    state.update(proof);persist()
        if result.returncode:raise RuntimeError('VK photo post retained for reconciliation: '+result.stderr[-600:])
        proof=json.loads(result.stdout.strip().splitlines()[-1])
        if proof.get('status')!='published':raise RuntimeError('VK did not verify the public image post')
        state.update(proof);persist();return state
    token=os.environ.get(cfg['token_env'])
    if not token:raise RuntimeError('VK publishing authorization is missing')
    group=int(cfg['group_id'])
    def call(method,**kwargs):
        response=requests.post('https://api.vk.com/method/'+method,data={'access_token':token,'v':'5.199',**kwargs},proxies=PROXIES,timeout=40)
        return checked(response,'VK')['response']
    def readback():
        job=BASE_DIR/'output'/('vk_readback_'+state['key']+'.json')
        save(job,{'group_id':group,'id':state['id'],'text':payload['text']})
        result=run_publisher(['docker','exec','video_factory_auth','python3','-u','/app/scripts/vk_post_readback.py',
            '/data/output/'+job.name],capture_output=True,text=True,timeout=180)
        if result.returncode:raise RuntimeError('VK post id retained for readback: '+result.stderr[-400:])
        state.update(json.loads(result.stdout.strip().splitlines()[-1]),status='published');persist()
    if state.get('id'):
        readback()
        return state
    identity=call('groups.getById',group_id=group)
    groups=identity.get('groups',[]) if isinstance(identity,dict) else identity
    if len(groups)!=1 or groups[0]['id']!=group:raise RuntimeError('VK community identity mismatch')
    result=call('wall.post',owner_id=-group,from_group=1,message=payload['text'][:15000],
        attachments=state.get('attachment',''),guid=state['key'])
    state.update(id=result['post_id'],status='posted_unverified',url=f'https://vk.ru/wall-{group}_{result["post_id"]}');persist()
    readback()
    return state


def reddit(cfg,payload,state,persist):
    from reddit_format import platform_body
    username=cfg['target'].removeprefix('u_')
    job=BASE_DIR/'output'/('reddit_native_job_'+state['key']+'.json')
    if not job.exists():
        payload=dict(payload)
        if cfg.get('external_links') is False:
            from reddit_adaptation import prepare_copy,prepare_video
            if payload.get('video'):
                payload['text']=json.loads((Path(payload['video']).parent/'script.json').read_text())['source_text']
            payload['text']=prepare_copy(payload,state['key'])
            if payload.get('video'):payload['video']=prepare_video(payload['video'],state['key'])
        raw=Path(payload.get('video') or payload['image']).resolve() if payload.get('video') or payload.get('image') else None
        save(job,{'key':state['key'],'username':username,'source_id':payload['source_id'],
            'title':payload['title'],'body':platform_body(payload['text']),
            'video':'/data/output/'+str(raw.relative_to((BASE_DIR/'output').resolve())) if payload.get('video') else None,
            'image':'/data/output/'+str(raw.relative_to((BASE_DIR/'output').resolve())) if payload.get('image') and not payload.get('video') else None,
            'sha256':hashlib.sha256(raw.read_bytes()).hexdigest() if raw else None})
    result=run_publisher(['docker','exec','video_factory_auth','python3','-u','/app/scripts/reddit_publisher.py',
        '/data/output/'+job.name],capture_output=True,text=True,timeout=480)
    native=BASE_DIR/'output'/('native_reddit_'+state['key']+'.json')
    if native.exists():
        proof=json.loads(native.read_text())
        if proof.get('status')=='rejected_by_platform' and proof.get('username')==username:
            state.update(status='rejected_by_platform',id=proof.get('id'),url=proof.get('url'),
                platform_rejection=proof.get('platform_rejection'));persist()
            configs=json.loads(CONFIG.read_text())
            for settings in configs.values():
                target=settings.get('publishing',{}).get('reddit',{})
                if target.get('target')=='u_'+username:
                    target.update(enabled=False,moderation_hold={'source_id':payload['source_id'],
                        'id':proof.get('id'),'reason':proof.get('last_error'),'observed_at':proof.get('platform_rejection_observed_at')})
            save(CONFIG,configs)
            raise RuntimeError('Reddit removed the post; profile publication paused pending an allowed format')
    if result.returncode:raise RuntimeError('Reddit receipt retained for reconciliation: '+result.stderr[-500:])
    acknowledged=json.loads(result.stdout.strip().splitlines()[-1])
    job_data=json.loads(job.read_text())
    if (not native.exists() or proof.get('status')!='published' or not proof.get('public_verified_at')
            or proof.get('username')!=username or proof.get('source_id')!=payload['source_id']
            or proof.get('sha256')!=job_data.get('sha256') or proof.get('url')!=acknowledged.get('url')):
        raise RuntimeError('Reddit has no matching durable public verification')
    state.update(status='published',id=proof['id'],url=proof['url'],
        public_verified_at=proof['public_verified_at'],published_at=proof.get('publication_attempted_at') or proof['public_verified_at'])
    persist();return state


NATIVE_NAMES={'instagram':'Instagram','tiktok':'TikTok'}


def telegram_link(brand):
    """Public link of the brand's own Telegram channel, from its configuration; None when not configured."""
    handle=json.loads(CONFIG.read_text()).get(brand,{}).get('telegram')
    return 'https://t.me/'+handle.lstrip('@') if isinstance(handle,str) and handle.strip('@') else None


def native_social(platform,cfg,payload,state,persist):
    """Own-account Instagram / TikTok publication through the signed-in factory browser."""
    from instagram_format import caption
    name=NATIVE_NAMES[platform]
    username=(cfg.get('target') or '').lstrip('@')
    if not username:raise RuntimeError(name+' account is not configured')
    job=BASE_DIR/'output'/(platform+'_native_job_'+state['key']+'.json')
    if not job.exists():
        media=payload.get('video') or payload.get('image')
        if not media:raise RuntimeError(name+' needs an image or a video')
        if platform=='tiktok' and not payload.get('video'):raise RuntimeError('TikTok publishes videos only')
        if not payload.get('video'):
            from instagram_adaptation import prepare_image
            media=prepare_image(media,state['key'])
        raw=Path(media).resolve()
        save(job,{'key':state['key'],'username':username,'source_id':payload['source_id'],
            'caption':caption(payload['title'],payload['text'],payload.get('telegram_link')),
            # Generated clips and illustrations carry the platform's AI label; archived source photos do not.
            'ai_label':bool(payload.get('video')) or '/post_images/source/' not in str(raw),
            'video':'/data/output/'+str(raw.relative_to((BASE_DIR/'output').resolve())) if payload.get('video') else None,
            'image':None if payload.get('video') else '/data/output/'+str(raw.relative_to((BASE_DIR/'output').resolve())),
            'sha256':hashlib.sha256(raw.read_bytes()).hexdigest()})
    result=run_publisher(['docker','exec','video_factory_auth','python3','-u','/app/scripts/'+platform+'_publisher.py',
        '/data/output/'+job.name],capture_output=True,text=True,timeout=900)
    native=BASE_DIR/'output'/('native_'+platform+'_'+state['key']+'.json')
    if native.exists():
        proof=json.loads(native.read_text())
        if proof.get('status') in ('publishing','posted_unverified') and proof.get('source_id')==payload['source_id']:
            state.update(status='posted_unverified',publish_requested_at=proof.get('share_attempted_at'));persist()
    if result.returncode:raise RuntimeError(name+' receipt retained for reconciliation: '+result.stderr[-500:])
    proof=json.loads(native.read_text()) if native.exists() else {}
    job_data=json.loads(job.read_text())
    if (proof.get('status')!='published' or proof.get('username')!=username or proof.get('source_id')!=payload['source_id']
            or proof.get('sha256')!=job_data['sha256'] or not proof.get('url')):
        raise RuntimeError(name+' has no matching durable profile verification')
    state.update(status='published',id=proof['code'],url=proof['url'],published_at=proof.get('published_at'),
        verified_at=proof.get('verified_at'));persist();return state


def instagram(cfg,payload,state,persist):return native_social('instagram',cfg,payload,state,persist)


def tiktok(cfg,payload,state,persist):return native_social('tiktok',cfg,payload,state,persist)


def reconcile_reddit():
    """Observe previously acknowledged posts even while new submissions are held."""
    path=DATA/'reddit_pending_jobs.json'
    if not path.exists():return
    failures=[]
    for name in json.loads(path.read_text()):
        job=BASE_DIR/'output'/name
        if job.parent!=BASE_DIR/'output':raise ValueError('Reddit reconciliation job outside output')
        data=json.loads(job.read_text());receipt=BASE_DIR/'output'/('native_reddit_'+data['key']+'.json')
        configs=json.loads(CONFIG.read_text())
        if any(x.get('publishing',{}).get('reddit',{}).get('moderation_hold')
            and x['publishing']['reddit'].get('target')=='u_'+data['username'] for x in configs.values()):continue
        previous=json.loads(receipt.read_text())
        if previous.get('status') in ('rejected_by_platform','retired_rejected'):continue
        if previous.get('status')!='published' and time.time()-previous.get('readback_attempted_at',0)<1800:continue
        result=run_publisher(['docker','exec','video_factory_auth','python3','-u','/app/scripts/reddit_publisher.py',
            '/data/output/'+job.name,'--read-only'],capture_output=True,text=True,timeout=120)
        if result.returncode:
            failures.append('Existing Reddit post is not publicly verified; no replacement sent: '+result.stderr[-220:]);continue
        proof=json.loads(receipt.read_text())
        if proof.get('status')!='published' or not proof.get('public_verified_at'):raise RuntimeError('Reddit public readback is missing')
        update_reddit_cursor(DATA/'distribution'/('reddit_'+data['username']+'_cursor.json'),
            data['source_id'],proof.get('publication_attempted_at') or proof['public_verified_at'],proof['url'])
        configs=json.loads(CONFIG.read_text())
        for cfg in configs.values():
            target=cfg.setdefault('publishing',{}).setdefault('reddit',{'target':'u_'+data['username']})
            if target.get('target')!='u_'+data['username']:continue
            if target.get('moderation_hold'):continue
            target.update(enabled=True,post_interval_seconds=86400,video_interval_seconds=86400)
            target.setdefault('post_enabled',False);target.setdefault('video_enabled',False)
            target['video_enabled' if proof.get('media_present') else 'post_enabled']=True
            target.pop('reason',None)
        save(CONFIG,configs)
    if failures:raise RuntimeError('; '.join(failures))


def publish(brand,platform,payload):
    if platform=='telegram' and payload.get('video') and (
            payload.get('source_id','').startswith('video-') or '/output/runs/' in str(payload['video'])):
        raise RuntimeError('Generated Shorts are published to video platforms; Telegram receives editorial posts')
    if brand=='migratoria':
        from editorial_policy import allowed
        if not allowed(payload.get('title','')+' '+payload.get('text','')):
            raise RuntimeError('Publication excluded by the owner topic policy')
    cfg=json.loads(CONFIG.read_text())[brand].get('publishing',{}).get(platform,{})
    if cfg.get('moderation_hold'):return {'status':'paused','platform':platform,'reason':cfg['moderation_hold']}
    if not cfg.get('enabled'):return {'status':'needs_authorization','platform':platform}
    reddit_cursor=DATA/'distribution'/('reddit_'+cfg.get('target','').removeprefix('u_')+'_cursor.json')
    if platform=='reddit':
        reddit_cursor.parent.mkdir(parents=True,exist_ok=True)
        sync_reddit_cursor(reddit_cursor,cfg.get('target','').removeprefix('u_'))
    if platform=='reddit' and reddit_cursor.exists():
        previous=json.loads(reddit_cursor.read_text())
        if previous.get('source_id')!=payload['source_id'] and time.time()-previous.get('last_post_at',0)<86400:
            return {'status':'scheduled','retry_after':previous['last_post_at']+86400}
    account_cursor=DATA/'distribution'/(platform+'_'+(cfg.get('target') or '').lstrip('@')+'_cursor.json')
    if platform in ('instagram','tiktok') and account_cursor.exists():
        # Both brands may share one account: one account-wide cadence, like the shared Reddit profile.
        previous=json.loads(account_cursor.read_text())
        wait=cfg.get('account_interval_seconds',21600)
        if previous.get('source_id')!=payload['source_id'] and time.time()-previous.get('last_post_at',0)<wait:
            return {'status':'scheduled','retry_after':previous['last_post_at']+wait}
    key=publication_key(brand,cfg,payload['source_id'],'video' if payload.get('video') else 'post')
    path=DATA/'distribution'/f'{brand}_{platform}_{key}.json'
    path.parent.mkdir(exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=json.loads(path.read_text()) if path.exists() else {'key':key,'status':'new','source_id':payload['source_id']}
        if state.get('status')=='rejected_by_platform':return state
        def persist():save(path,state)
        if not payload.get('video'):
            if 'payload' not in state:
                frozen=dict(payload)
                previous_job=BASE_DIR/'output'/('vk_readback_'+key+'.json')
                if platform=='vk' and state.get('id') and previous_job.exists():
                    frozen['text']=json.loads(previous_job.read_text())['text']
                state['payload']=frozen;persist()
            payload=state['payload']
        if platform in ('instagram','tiktok') and payload.get('video'):
            # The narration promises a link in the description; Instagram and TikTok show it as plain text.
            payload=dict(payload,telegram_link=telegram_link(brand))
        if platform=='telegram':result=telegram(cfg,payload,state,persist)
        elif platform=='vk':result=vk(cfg,payload,state,persist)
        elif platform=='reddit':result=reddit(cfg,payload,state,persist)
        elif platform=='instagram':result=instagram(cfg,payload,state,persist)
        elif platform=='tiktok':result=tiktok(cfg,payload,state,persist)
        else:raise RuntimeError('This platform has not passed its authorization and publication checks')
        if platform=='reddit' and result.get('status')=='published':
            update_reddit_cursor(reddit_cursor,payload['source_id'],result.get('published_at') or result['public_verified_at'],result['url'])
        if platform in ('instagram','tiktok') and result.get('status')=='published':
            update_reddit_cursor(account_cursor,payload['source_id'],result.get('published_at') or time.time(),result['url'])
        state['confirmed_at']=time.time();persist()
        if result.get('status')=='published' and result.get('url'):_announce(brand,platform,payload,result)
        return result


def migration_post(for_video=False):
    from migratoria_editor import db
    cfg=json.loads(CONFIG.read_text())['migratoria']
    if cfg.get('editorial_hold'):return
    if not cfg.get('publishing',{}).get('telegram',{}).get('enabled'):return
    with db() as con:
        last=con.execute('SELECT max(published_at) FROM posts WHERE telegram_id IS NOT NULL').fetchone()[0]
        # A video due now must not wait for an unrelated Telegram timer while
        # promising a guide that has not been posted. Allow its reviewed guide
        # earlier, with an hour between Telegram posts to prevent a burst.
        if last and time.time()-last<(3600 if for_video else 10800):return
        row=con.execute("SELECT * FROM posts WHERE state='ready' AND telegram_id IS NULL AND created_at>? ORDER BY id LIMIT 1",(time.time()-86400,)).fetchone()
        if not row:return
        result=publish('migratoria','telegram',add_post_image('migratoria',migration_payload(row)))
        if result['status']=='published':
            con.execute("UPDATE posts SET telegram_id=?,state='posted',published_at=? WHERE id=?",(result['id'],time.time(),row['id']))
            print(json.dumps({'brand':'migratoria','telegram_post':result['url']},ensure_ascii=False),flush=True)


def migration_payload(row):
    payload={'source_id':'migration-post-'+str(row['id']),'title':row['title'],'text':row['text']}
    evidence=json.loads(row['evidence']);action=evidence.get('action_link',{})
    label=action.get('label');url=action.get('url')
    if label and label in row['text'] and any(d['url']==url for d in evidence['documents']):
        payload.update(text=html.escape(row['text']).replace(html.escape(label),'<a href="'+html.escape(url,quote=True)+'">'+html.escape(label)+'</a>',1),parse_mode='HTML')
    return payload


def add_post_image(brand,payload,media=None):
    """Preserve source media; only missing media triggers a new illustration."""
    if payload.get('image') or payload.get('video'):return payload
    from post_images import cached_image,ensure_image
    path=cached_image(brand,payload['source_id'])
    if not path and media:
        # Archive source photos for distribution, without generating replacements.
        items=json.loads(media) if isinstance(media,str) else media
        item=next((x for x in items if isinstance(x,dict) and x.get('type','photo')=='photo'),None)
        if item:
            response=requests.get(item['url'],proxies=PROXIES,timeout=40)
            if response.ok and response.headers.get('content-type','').startswith('image/'):
                folder=BASE_DIR/'output/post_images/source';folder.mkdir(parents=True,exist_ok=True)
                target=folder/(hashlib.sha256((brand+payload['source_id']).encode()).hexdigest()+'.jpg')
                target.write_bytes(response.content);path=str(target)
    if not path:path=ensure_image(brand,payload['source_id'],payload['title'],payload['text'])
    return {**payload,'image':path}

STALE_PENDING=12*3600


def abandon_if_stale(receipt,value,cursor_path,cursor,brand,platform,now=None):
    """Stop a post that stayed unverifiable for 12 hours from blocking its platform queue forever.

    Fail-closed rules (never resend an uncertain post) held WrongDev's VK queue for 50 hours: one photo
    post that could not be found again kept every newer post waiting. The uncertain source is never
    sent again; it is marked abandoned, the cursor moves past it, and the owner is told it may be live."""
    now=now or time.time()
    marks=[value[k] for k in ('publish_requested_at','share_attempted_at','first_pending_at') if value.get(k)]
    if not marks:
        value['first_pending_at']=now;save(receipt,value);return False
    if now-min(marks)<=STALE_PENDING:return False
    value.update(status='abandoned_unverified',abandoned_at=now);save(receipt,value)
    digits=re.search(r'(\d+)$',value.get('source_id',''))
    if digits:
        cursor['last_id']=max(cursor.get('last_id',0),int(digits[1]))
        save(cursor_path,{**cursor,'publication':cursor.get('publication')})
    try:
        import owner_notify
        owner_notify.send(f"⚠️ <b>{owner_notify.label(brand)}</b> · {owner_notify.platform_name(platform)}: пост {value.get('source_id')} "
            f"не удалось подтвердить за 12 часов. Повторно он не отправляется (возможно, уже опубликован), очередь продолжена.",
            key=f"abandoned:{value.get('source_id')}:{platform}",cooldown=7*86400)
    except Exception:pass
    return True


def post_age(row,now):
    """Seconds since a source post appeared; None when its date cannot be read."""
    stamp=row.get('published_at') or row.get('posted_date')
    if isinstance(stamp,(int,float)):return now-stamp
    try:
        from datetime import datetime,timezone
        value=datetime.fromisoformat(str(stamp))
        return now-(value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:return None


def distribute_posts(budget=None):
    import sqlite3
    budget=budget if budget is not None else [MAX_BROWSER_PUBLICATIONS]
    from script_generator import DB_PATH
    from migratoria_editor import db
    configs=json.loads(CONFIG.read_text())
    failures=[]
    for brand,cfg in configs.items():
        targets=[p for p in ('vk','reddit','instagram') if cfg.get('publishing',{}).get(p,{}).get('enabled')
            and cfg['publishing'][p].get('post_enabled',True)]
        if not targets:continue
        if brand=='wrongdev':
            with sqlite3.connect(f'file:{DB_PATH}?mode=ro',uri=True) as con:
                con.row_factory=sqlite3.Row
                columns={r[1] for r in con.execute('PRAGMA table_info(posts)')}
                media='media_urls' if 'media_urls' in columns else 'NULL AS media_urls'
                rows=[dict(r) for r in con.execute("SELECT id,title,processed_text AS text,posted_date,"+media+" FROM posts WHERE status='posted' ORDER BY id DESC LIMIT 20")]
        else:
            with db() as con:rows=[dict(r) for r in con.execute('SELECT * FROM posts WHERE telegram_id IS NOT NULL ORDER BY id DESC LIMIT 20')]
        for platform in targets:
            if budget[0]<=0:break
            target=cfg['publishing'][platform].get('target',cfg['publishing'][platform].get('group_id'))
            if target_held(platform,target,'post'):continue
            cursor_path=DATA/'distribution'/f'{brand}_{platform}_post_cursor.json'
            cursor_path.parent.mkdir(exist_ok=True)
            cursor=json.loads(cursor_path.read_text()) if cursor_path.exists() else {'last_id':0,'last_post_at':0}
            pending=[]
            for receipt in (DATA/'distribution').glob(brand+'_'+platform+'_*.json'):
                if not re.fullmatch(re.escape(brand+'_'+platform+'_')+r'[a-f0-9]{64}\.json',receipt.name):continue
                value=json.loads(receipt.read_text())
                if value.get('status') in ('preparing','posted_unverified','sending','publishing') and not value.get('source_id','').startswith('video-'):
                    if abandon_if_stale(receipt,value,cursor_path,cursor,brand,platform):continue
                    pending.append(value)
            if not pending and time.time()-cursor['last_post_at']<cfg['publishing'][platform].get('post_interval_seconds',10800):continue
            now=time.time()
            fresh=[r for r in rows if r['id']>cursor['last_id'] and ((post_age(r,now) if post_age(r,now) is not None else 0)<=POST_WINDOW)]
            if pending:
                source_id=pending[0]['source_id']
                fresh=[r for r in rows if ('migration-post-' if brand=='migratoria' else 'post-')+str(r['id'])==source_id]
                if not fresh:
                    failures.append(brand+'/'+platform+': pending publication source needs reconciliation');continue
            if not fresh:continue
            # Oldest unsent first, one per tick, so a burst of posts all arrive; POST_WINDOW keeps a new destination free of old history.
            row=fresh[-1]
            text=migration_payload(row)['text'] if brand=='migratoria' else row['text']
            soup=BeautifulSoup(text,'html.parser')
            for a in soup.select('a[href]'):
                url=a['href']
                if url.startswith(('https://','http://')):a.replace_with(a.get_text()+' '+url)
            text=html.unescape(soup.get_text())
            source_id=('migration-post-' if brand=='migratoria' else 'post-')+str(row['id'])
            try:
                payload={'source_id':source_id,'title':row['title'],'text':text}
                # Already submitted posts retain their frozen original payload.
                if not pending:payload=add_post_image(brand,payload,row.get('media_urls'))
                result=publish(brand,platform,payload)
            except BrowserBusy:break
            except Exception as exc:
                if browser_contention(exc):break
                budget[0]-=1;note_failure(platform,target,'post',exc)
                failures.append(brand+'/'+platform+': '+str(exc));_alert(brand,platform,exc);continue
            if result['status']=='published':
                budget[0]-=1;note_success(platform,target,'post')
                save(cursor_path,{'last_id':max(row['id'],cursor['last_id']),
                    'last_post_at':result.get('published_at') or result.get('publish_requested_at') or time.time(),'publication':result['url']})
                print(json.dumps({'brand':brand,'platform':platform,'post':result['url']}),flush=True)
    if failures:raise RuntimeError('; '.join(failures))


def abandon_video_if_stale(path,value,brand,platform,now):
    """An uncertain video send older than 12 h stops holding its queue. It is never sent again: it may be live."""
    marks=[value[k] for k in ('publish_requested_at','share_attempted_at','first_pending_at') if value.get(k)]
    if not marks:
        value['first_pending_at']=now;save(path,value);return False
    if now-min(marks)<=STALE_PENDING:return False
    value.update(status='abandoned_unverified',abandoned_at=now);save(path,value)
    try:
        import owner_notify
        owner_notify.send(f"⚠️ <b>{owner_notify.label(brand)}</b> · {owner_notify.platform_name(platform)}: ролик {value.get('source_id')} "
            f"не удалось подтвердить за 12 часов. Повторно не отправляется (возможно, уже опубликован), очередь продолжена.",
            key=f"abandoned:{value.get('source_id')}:{platform}",cooldown=7*86400)
    except Exception:pass
    return True


def video_queue(brand,pcfg,platform,manifests,now):
    """(pending, unsent) for one account, newest first. Pending receipts are reconciled before anything new is sent."""
    pending,unsent=[],[]
    fresh=[(p,m) for p,m in manifests if m.get('status')=='published' and m.get('youtube_url') and m.get('brand','wrongdev')==brand
        and now-m.get('completed_at',m.get('started_at',0))<=VIDEO_WINDOW]
    for p,m in sorted(fresh,key=lambda item:-item[1].get('completed_at',item[1].get('started_at',0))):
        path,receipt=receipt_of(brand,platform,pcfg,'video-'+m['run_id'])
        status=(receipt or {}).get('status')
        if status in TERMINAL_RECEIPTS:continue
        if status in (None,'new'):unsent.append((p,m))
        elif not abandon_video_if_stale(path,receipt,brand,platform,now):pending.append((p,m))
    return pending,unsent


def distribute_videos(budget=None):
    """Every recent video to every enabled account, one per account per tick, within that account's cadence.

    Before 2026-11-02 only each brand's newest video was offered, so with a 24 h cadence 2 of 13 videos reached Instagram
    and 2 of 13 TikTok: whatever was newest when the cadence opened went out, the rest never did."""
    budget=budget if budget is not None else [MAX_BROWSER_PUBLICATIONS]
    configs=json.loads(CONFIG.read_text())
    failures=[]
    manifests=[(p,json.loads(p.read_text())) for p in (BASE_DIR/'output/runs').glob('*/manifest.json')]
    def last_post(brand,platform):
        path=DATA/'distribution'/f'{brand}_{platform}_video_cursor.json'
        try:return json.loads(path.read_text()).get('last_post_at',0)
        except (OSError,ValueError):return 0
    from video_quality import require_artifact
    for platform in ('vk','instagram','tiktok','reddit'):
        # The brand served longest ago goes first, so two brands on one account take turns.
        for brand,cfg in sorted(configs.items(),key=lambda item:last_post(item[0],platform)):
            if budget[0]<=0:break
            pcfg=cfg.get('publishing',{}).get(platform,{})
            if not pcfg.get('enabled') or not pcfg.get('video_enabled',False):continue
            target=pcfg.get('target',pcfg.get('group_id'))
            if target_held(platform,target,'video'):continue
            now=time.time()
            pending,unsent=video_queue(brand,pcfg,platform,manifests,now)
            queue=pending or (unsent if now-last_post(brand,platform)>=pcfg.get('video_interval_seconds',10800) else [])
            for manifest_path,manifest in queue:
                try:require_artifact(manifest)
                except Exception as exc:
                    print(json.dumps({'brand':brand,'platform':platform,'skipped':manifest['run_id'],'reason':str(exc)[:160]},ensure_ascii=False),flush=True)
                    continue
                meta=json.loads((manifest_path.parent/'script.json').read_text())['youtube_meta']
                try:
                    result=publish(brand,platform,{'source_id':'video-'+manifest['run_id'],'title':meta['title'],
                        'text':meta['description'],'video':manifest['video']})
                except BrowserBusy:break
                except Exception as exc:
                    if browser_contention(exc):break
                    budget[0]-=1;note_failure(platform,target,'video',exc)
                    failures.append(brand+'/'+platform+': '+str(exc));_alert(brand,platform,exc);break
                if result['status']=='published':
                    budget[0]-=1;note_success(platform,target,'video')
                    save(DATA/'distribution'/f'{brand}_{platform}_video_cursor.json',{'run_id':manifest['run_id'],'last_post_at':time.time(),'publication':result['url']})
                    print(json.dumps({'brand':brand,'platform':platform,'video':result['url']}),flush=True)
                break
    if failures:raise RuntimeError('; '.join(failures))


def distribute_articles():
    cfg=json.loads(CONFIG.read_text())['migratoria'].get('publishing',{}).get('dzen',{})
    if not cfg.get('enabled'):return
    cursor=DATA/'distribution/migratoria_dzen_article_cursor.json';cursor.parent.mkdir(exist_ok=True)
    previous=json.loads(cursor.read_text()) if cursor.exists() else {}
    if time.time()-previous.get('last_post_at',0)<86400:return
    folders=sorted((BASE_DIR/'output/articles').glob('migratoria_*/article.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    for path in folders:
        article=json.loads(path.read_text())
        if (article.get('status')!='draft_ready' or not article.get('review',{}).get('supported')
                or article.get('target_channel')!=cfg['channel_id'] or time.time()-article['created_at']>72*3600):continue
        folder=path.parent;cover=folder/'cover.jpg'
        if not cover.exists():
            import shutil
            from post_images import ensure_image
            image=ensure_image('migratoria','migration-post-'+str(article['source_post_id']),article['title'],article['intro'])
            shutil.copyfile(image,cover)
        result=run_publisher(['docker','exec','video_factory_auth','python3','-u','/app/scripts/dzen_publisher.py',
            '/data/output/'+str(folder.relative_to(BASE_DIR/'output'))],capture_output=True,text=True,timeout=480)
        if result.returncode:
            _alert('migratoria','dzen','Dzen publication retained for retry: '+result.stderr[-650:])
            raise RuntimeError('Dzen publication retained for retry: '+result.stderr[-650:])
        posted=json.loads(result.stdout.strip().splitlines()[-1])
        if posted.get('status')!='published':raise RuntimeError('Dzen did not confirm publication')
        proof=json.loads((folder/'dzen_receipt.json').read_text())
        article.update(status='published',url=posted['url']);save(path,article)
        save(cursor,{'last_source_post_id':article['source_post_id'],'last_post_at':proof['public_verified_at'],'url':posted['url']})
        print(json.dumps({'platform':'dzen','published':posted['url']},ensure_ascii=False),flush=True)
        try:
            import owner_notify
            owner_notify.publication('migratoria','dzen','article',article['title'],posted['url'])
        except Exception:pass
        return

if __name__=='__main__':
    migration_post();distribute_posts();distribute_videos();distribute_articles()
