#!/usr/bin/env python3
"""Persistent server scheduler. Never advances a publication slot on generation alone."""
import fcntl
import json
import os
import time
import uuid
from pathlib import Path
from datetime import datetime


def nothing_due(base,now=None):
    """Standard library only: the minute timer usually finds nothing to do, and the imports below cost about 1 s of CPU
    plus page faults on a slow disk, 1440 times a day. Any doubt means the full tick runs."""
    now=now or time.time()
    try:
        configs=json.loads((base/'data/media_brands.json').read_text())
        state=json.loads((base/'data/media_schedule.json').read_text())
        if not configs.get('migratoria',{}).get('youtube_channel_id'):return False
        for brand,cfg in configs.items():
            if not cfg.get('video_enabled') or cfg.get('quality_hold'):continue
            slot=state.get(brand)
            if slot is None:return False
            if slot.get('job') or now>=slot['next_due']-1800:
                if now>=slot.get('retry_after',0) or slot.get('error')=='No fresh verified Migratoria post is ready for video':return False
        return True
    except (OSError,ValueError,KeyError,AttributeError):return False


def publisher_is_waiting(base,now=None):
    """A publisher wants the shared browser: no new scene starts this minute (see browser_process.wait_for_lease)."""
    try:return (now or time.time())-(base/'output/.publisher-waiting').stat().st_mtime<120
    except OSError:return False


if __name__=='__main__':
    _base=Path(os.getenv('VIDEO_FACTORY_DIR',Path(__file__).resolve().parent.parent))
    if nothing_due(_base) or publisher_is_waiting(_base):raise SystemExit(0)

from autonomous_video_pipeline import run_pipeline, get_next_topic, BASE_DIR
from youtube_uploader import upload_video, save, get_session, channel_identity

DATA=BASE_DIR/'data'
CONFIG=DATA/'media_brands.json'
STATE=DATA/'media_schedule.json'


def notify(name,*args):
    """Owner reports are best effort: they can never fail or undo a publication."""
    try:
        import owner_notify
        getattr(owner_notify,name)(*args)
    except Exception:pass


def next_released_run(brand):
    """Return the next held run the owner released, so it is finished rather than rendered again.

    DATA/released_held_runs.json is {brand: [run_id, ...]}. An entry counts only while its manifest
    still says held_for_review for this brand; anything else is dropped. The hold on the run's
    source is lifted when the run is taken. Every scene of a held run is already generated, so
    resuming it spends no video credits; the visual gate still decides whether it publishes."""
    path=DATA/'released_held_runs.json'
    if not path.exists():return None
    queue=json.loads(path.read_text())
    while queue.get(brand):
        run_id=queue[brand].pop(0)
        save(path,queue)
        manifest_file=BASE_DIR/'output'/'runs'/run_id/'manifest.json'
        if not manifest_file.exists():continue
        manifest=json.loads(manifest_file.read_text())
        source=manifest.get('source_post_id')
        if manifest.get('status')!='held_for_review' or manifest.get('brand')!=brand or source is None:continue
        holds_path=DATA/'review_held_video_sources.json'
        holds=json.loads(holds_path.read_text()) if holds_path.exists() else {}
        if holds.get(brand,{}).pop(str(source),None) is not None:save(holds_path,holds)
        if brand=='migratoria':
            from migratoria_editor import db
            with db() as con:con.execute('UPDATE posts SET video_exclusion_reason=NULL WHERE id=? AND video_exclusion_reason LIKE ?',(source,'%:'+run_id))
        return {'run_id':run_id,'source_post_id':source}
    return None


def tick():
    DATA.mkdir(exist_ok=True)
    with (DATA/'.media-worker.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return
        configs=json.loads(CONFIG.read_text())
        state=json.loads(STATE.read_text()) if STATE.exists() else {}
        migration=configs.get('migratoria',{})
        token=Path(migration.get('youtube_token','/nonexistent'))
        if not migration.get('youtube_channel_id') and token.is_file() and os.access(token,os.R_OK):
            try:
                identity=channel_identity(get_session(token))
                if not any(word in identity['title'].lower() for word in ('мигратор','migrator')):
                    raise RuntimeError('Migratoria authorization selected a different channel: '+identity['title'])
                migration.update(youtube_channel_id=identity['id']);migration.pop('auth_error',None)
                save(CONFIG,configs)
                print(json.dumps({'brand':'migratoria','authorized_channel':identity}),flush=True)
            except Exception as exc:
                migration['auth_error']=type(exc).__name__+': '+str(exc)[:250]
                save(CONFIG,configs)
        # Serve the oldest due slot first when both channels need the one browser.
        for brand,cfg in sorted(configs.items(),key=lambda item:state.get(item[0],{}).get('next_due',item[1].get('anchor_epoch',0)+10800)):
            if not cfg.get('video_enabled'): continue
            if cfg.get('quality_hold'): continue
            slot=state.setdefault(brand,{'next_due':cfg['anchor_epoch']+10800})
            now=time.time()
            if now < slot['next_due']-1800 and not slot.get('job'):continue
            quota_path=DATA/'tts_quota.json'
            if cfg.get('audio_mode','gemini')=='gemini' and quota_path.exists():
                quota=json.loads(quota_path.read_text())
                if quota.get('retry_at',0)>now:
                    slot.update(hold='tts_daily_quota_exhausted',retry_after=quota['retry_at'])
                    save(STATE,state)
                    continue
            if slot.pop('hold',None)=='tts_daily_quota_exhausted':slot.pop('retry_after',None)
            if now < slot.get('retry_after',0):
                # A newly posted, verified guide resolves an empty-source wait.
                # Keep generation/provider backoff intact for real failed jobs.
                if (brand=='migratoria' and not slot.get('job')
                        and slot.get('error')=='No fresh verified Migratoria post is ready for video'):
                    from migratoria_editor import next_video_source
                    try:next_video_source()
                    except RuntimeError:continue
                    slot.pop('retry_after',None)
                else:continue
            os.environ['MEDIA_BRAND']=brand
            os.environ['MEDIA_AUDIO_MODE']=cfg.get('audio_mode','gemini')
            if not slot.get('job'):
                try:released=next_released_run(brand)
                except Exception as exc:
                    released=None;print(json.dumps({'brand':brand,'released_run_error':type(exc).__name__+': '+str(exc)[:200]}),flush=True)
                if released:
                    slot.update(job=released['run_id'],source_post_id=released['source_post_id'],attempts=0)
                    slot.pop('error',None);save(STATE,state)
            if not slot.get('job'):
                try:
                    try:
                        topic=get_next_topic()
                    except RuntimeError:
                        if brand!='migratoria':raise
                        from content_distribution import migration_post
                        migration_post(for_video=True)
                        topic=get_next_topic()
                    slot['source_post_id']=topic['id']
                except RuntimeError as exc:
                    slot.update(error=str(exc),retry_after=now+900);save(STATE,state);continue
                slot['job']=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
                slot['attempts']=0
                save(STATE,state)
            try:
                os.environ['MEDIA_BRAND']=brand
                os.environ['MEDIA_AUDIO_MODE']=cfg.get('audio_mode','gemini')
                run_id=slot['job']; folder=BASE_DIR/'output'/'runs'/run_id
                manifest_file=folder/'manifest.json'
                manifest=json.loads(manifest_file.read_text()) if manifest_file.exists() else None
                if manifest is None:
                    manifest=run_pipeline(post_id=slot.get('source_post_id'),job_id=run_id)
                elif manifest['status'] not in ('preview_ready','published'):
                    manifest=run_pipeline(resume=run_id)
                if time.time()<slot['next_due']:
                    slot['prepared_at']=time.time();save(STATE,state);continue
                script=json.loads((folder/'script.json').read_text())
                meta=script['youtube_meta']
                from video_quality import require_artifact
                require_artifact(manifest)
                if brand=='migratoria':
                    from editorial_policy import allowed
                    if not allowed(meta['title']+' '+meta['description']+' '+script['voiceover_ru']):
                        raise RuntimeError('Video excluded by the owner topic policy')
                    from youtube_uploader import upload_video_studio
                    uploaded=upload_video_studio(manifest['video'],meta['title'],meta['description'],meta['tags'],cfg,brand)
                    manifest.update(status='published',youtube_url=uploaded['url'],publication=uploaded,brand=brand)
                else:
                    if cfg.get('youtube_uploader')=='studio':
                        from youtube_uploader import upload_video_studio
                        uploaded=upload_video_studio(manifest['video'],meta['title'],meta['description'],meta['tags'],cfg,brand)
                    else:
                        uploaded=upload_video(manifest['video'],meta['title'],meta['description'],meta['tags'],'public',
                            token_path=cfg['youtube_token'],expected_channel_id=cfg['youtube_channel_id'],
                            category_id=cfg.get('category_id','28'))
                    manifest.update(status='published',youtube_url=uploaded['url'],publication=uploaded,brand=brand)
                save(manifest_file,manifest)
                # Three hours from the real confirmed publication. A delayed job
                # must not leave the following slot only a few minutes away.
                published_at=time.time()
                slot.update(next_due=published_at+10800,last_published_at=published_at,last_publication=uploaded)
                if brand=='migratoria':
                    from migratoria_editor import db
                    with db() as con:con.execute('UPDATE posts SET video_run=? WHERE id=?',(run_id,manifest['source_post_id']))
                slot.pop('job',None);slot.pop('error',None);slot.pop('retry_after',None)
                save(STATE,state)
                print(json.dumps({'brand':brand,'published':uploaded['url'],'next_due':slot['next_due']}),flush=True)
                notify('published',brand,meta['title'],uploaded['url'],slot['next_due'])
            except Exception as exc:
                slot['attempts']+=1
                message=str(exc)
                slot.update(error=type(exc).__name__+': '+(message if len(message)<=1400 else message[:200]+' […] '+message[-1100:]),retry_after=time.time()+900)
                from video_provider_pool import BeforeSubmissionFailure
                if isinstance(exc,BeforeSubmissionFailure):slot['retry_after']=time.time()+60
                from bundled_voiceover import NarrationNeedsReview
                from script_review import ScriptNeedsReview
                from video_quality import VisualReviewNeedsReview
                if isinstance(exc,(NarrationNeedsReview,ScriptNeedsReview,VisualReviewNeedsReview)):
                    # Hold the failed source, not the whole channel forever.
                    # No publication occurred and the due slot remains unchanged.
                    path=DATA/'review_held_video_sources.json'
                    holds=json.loads(path.read_text()) if path.exists() else {}
                    holds.setdefault(brand,{})[str(slot['source_post_id'])]={
                        'run_id':run_id,'reason':slot['error'],'held_at':time.time()}
                    save(path,holds)
                    if manifest_file.exists():
                        held=json.loads(manifest_file.read_text());held.update(status='held_for_review',error=slot['error']);save(manifest_file,held)
                    if brand=='migratoria':
                        from migratoria_editor import db
                        kind='visual_review_rejected:' if isinstance(exc,VisualReviewNeedsReview) else 'narration_requires_review:'
                        with db() as con:con.execute('UPDATE posts SET video_exclusion_reason=? WHERE id=?',(kind+run_id,slot['source_post_id']))
                    notify('held',brand,run_id,slot['error'])
                    slot.update(last_held_job=slot.pop('job'),retry_after=time.time()+60)
                    slot.pop('source_post_id',None)
                else:notify('retrying',brand,slot['attempts'],slot['error'])
                save(STATE,state)
                print(json.dumps({'brand':brand,'status':'retry_pending','error':slot['error']}),flush=True)
        save(STATE,state)

if __name__=='__main__': tick()
