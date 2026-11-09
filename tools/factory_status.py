"""Read-only operating snapshot for Hermes; never mistakes a live container for publication."""
import json,os,sqlite3,subprocess,time
from pathlib import Path

BASE=Path(os.getenv('VIDEO_FACTORY_DIR','/home/gut/hermes-workspace/video_factory'))


def read_json(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {}


def latest_post(database,query):
    if not database.exists():return None
    try:
        with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=5) as con:
            con.row_factory=sqlite3.Row;row=con.execute(query).fetchone()
            return dict(row) if row else None
    except sqlite3.Error as exc:return {'unavailable':type(exc).__name__}


def snapshot():
    configs=read_json(BASE/'data/media_brands.json');schedule=read_json(BASE/'data/media_schedule.json')
    brands={}
    for brand,cfg in configs.items():
        slot=schedule.get(brand,{})
        job=slot.get('job');folder=BASE/'output/runs'/str(job or '')
        manifest=read_json(folder/'manifest.json') if job else {}
        script=read_json(folder/'script.json') if job else {}
        own={'lvl':('lvl_enabled','lvl-worker.timer'),'storywobble':('story_enabled','story-worker.timer')}.get(brand,('video_enabled','video-autoposter.timer'))
        brands[brand]={'video_enabled':bool(cfg.get(own[0])),
            'automation_enabled':bool(cfg.get(own[0])),
            'worker':own[1],'quality_hold':cfg.get('quality_hold'),
            'enabled_destinations':[name for name,target in cfg.get('publishing',{}).items() if target.get('enabled')],
            'next_due':slot.get('next_due'),'prepare_lead_seconds':cfg.get('prepare_lead_seconds',1800),
            'publish_interval_seconds':cfg.get('publish_interval_seconds'),
            'last_published_at':slot.get('last_published_at'),'last_video_url':slot.get('last_publication',{}).get('url'),
            'job':job,'job_status':manifest.get('status'),'motion_system':script.get('motion_system'),
            'finished_scenes':len(manifest.get('scenes',[])),'retry_after':slot.get('retry_after'),'error':manifest.get('error') if manifest else slot.get('error'),
            'last_error':slot.get('error'),'job_updated_at':(folder/'manifest.json').stat().st_mtime if (folder/'manifest.json').exists() else None}
    if 'wrongdev' in brands:
        brands['wrongdev']['latest_telegram_post']=latest_post(BASE.parent/'tg_autoposter/data/tg_autoposter.db',
            "SELECT id,title,posted_date FROM posts WHERE status='posted' ORDER BY posted_date DESC LIMIT 1")
    if 'migratoria' in brands:
        brands['migratoria']['latest_telegram_post']=latest_post(BASE/'data/migratoria.db',
            'SELECT id,title,telegram_id,published_at FROM posts WHERE telegram_id IS NOT NULL ORDER BY published_at DESC LIMIT 1')
    if 'lvl' in brands:
        queue=[]
        cfg=configs['lvl'];targets=[p for p,t in cfg.get('publishing',{}).items() if t.get('enabled') and t.get('video_enabled',True)]
        for path in sorted((BASE/'output/lvl_runs').glob('*/manifest.json')):
            item=read_json(path)
            if item.get('status') not in ('ready','waiting_media'):continue
            assets=read_json(path.parent/'hybrid/assets.json')
            reviews=item.get('visual_reviews',{})
            queue.append({'run_id':item.get('run_id'),'status':item.get('status'),'motion_system':item.get('motion_system'),
                'finished_inserts':len(assets.get('scenes',{})),
                'reviewed_languages':[lang for lang,r in reviews.items() if r.get('publishable') and r.get('video_sha256')==item.get('sha256',{}).get(lang)],
                'pending_destinations':[p+':'+lang for lang in cfg.get('post_hour_utc',{}) for p in targets if p+':'+lang not in item.get('published',{})]})
        brands['lvl']['queue']=queue
        brands['lvl']['schedule_mode']='interval' if cfg.get('publish_interval_seconds') else 'daily_slots'
        worker=read_json(BASE/'data/lvl/worker.json')
        brands['lvl']['hybrid_retries']=worker.get('hybrid_retries',{})
        brands['lvl']['worker_state']={k:worker.get(k) for k in
            ('last_tick_at','last_produced_at','produce_failed_at','last_produce_error')}
        posts=[read_json(p) for p in (BASE/'output/lvl_posts').glob('*/post.json')]
        published=[p for p in posts if p.get('status')=='published']
        latest=max(published,key=lambda p:p.get('published_at',0),default={})
        brands['lvl']['latest_telegram_post']={k:latest.get(k) for k in ('run_id','title','lang','published_at')}
        brands['lvl']['latest_telegram_post']['url']=(latest.get('publication') or {}).get('url')
        brands['lvl']['telegram_pending']=[{'run_id':p.get('run_id'),'status':p.get('status')} for p in posts if p.get('status') in ('ready','sending')]
    if 'storywobble' in brands:
        runs=[read_json(p) for p in sorted((BASE/'output/story_runs').glob('*/manifest.json'))]
        brands['storywobble']['queue']=[{k:m.get(k) for k in ('run_id','status','title','error','published')} for m in runs[-4:]]
        brands['storywobble']['worker_state']=read_json(BASE/'data/storywobble/worker.json')

    units=('hermes-backend.service','hermes-gateway.service','tg-autoposter.service','video-autoposter.timer',
           'media-research.timer','media-distribution.timer','lvl-worker.timer','story-worker.timer',
           'media-images.timer','media-health.timer','media-analytics.timer')
    result=subprocess.run(['systemctl','--user','show',*units,'--property=Id,ActiveState,SubState,UnitFileState'],
                          capture_output=True,text=True,timeout=15)
    services=[]
    for block in result.stdout.strip().split('\n\n'):
        values=dict(line.split('=',1) for line in block.splitlines() if '=' in line)
        if values:services.append(values)
    health=read_json(BASE/'data/media_health.json')
    return {'checked_at':time.time(),'brands':brands,'units':services,
            'health':{k:health.get(k) for k in ('checked_at','status','problems')},
            'research':read_json(BASE/'data/research_status.json'),'distribution':read_json(BASE/'data/distribution_status.json')}


if __name__=='__main__':print(json.dumps(snapshot(),ensure_ascii=False,indent=2))
