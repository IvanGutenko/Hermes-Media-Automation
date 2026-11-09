"""Included-credit Flow accounts, then Vids; uncertain requests never fail over."""
import fcntl
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from script_generator import BASE_DIR
from youtube_uploader import save
from browser_process import run as run_browser

OUTPUT=BASE_DIR/'output';DATA=BASE_DIR/'data'
DEFAULTS=[
 {'id':'flow0','provider':'flow','project_url':'https://flow.google.com/u/0/project/23388e4b-7da0-42d5-97a2-081e90d27c36'},
 {'id':'flow1','provider':'flow','project_url':'https://flow.google.com/u/1/'},
 {'id':'vids1','provider':'vids','account':1}]
SAFE_FAILURES={'quota_exhausted','unsupported_country','feature_unavailable','generation_failed'}
# A submitted request that is still unresolved after this long is lost (Flow answers within minutes). Without a
# limit, one lost request kept its brand's video production retrying the same dead request for ever.
RETAINED_MAX_SECONDS=8*3600


class BeforeSubmissionFailure(RuntimeError):
    """A stopped worker's durable receipt proves that no request was submitted."""


class IncludedProvidersUnavailable(RuntimeError):
    """Every eligible included provider is on an observed availability hold."""
    def __init__(self,details,retry_after):
        self.retry_after=retry_after
        summary='; '.join(name+'='+str(value.get('reason','unknown')) for name,value in details.items())
        super().__init__('All included video providers are unavailable ('+summary+'); automatic recheck '+
            time.strftime('%Y-%m-%d %H:%M UTC',time.gmtime(retry_after)))


def artifact(data,prompt,voiceover_ru=None):
    if (data.get('status')!='completed' or data.get('prompt_sha256')!=hashlib.sha256(prompt.encode()).hexdigest()
            or not data.get('prompt_verified_in_editor') or not data.get('media_id')
            or data['media_id'] in data.get('baseline_media_ids',[])
            or not (data.get('submitted_at') or data.get('submission_attempted_at'))
            or data.get('voiceover_ru')!=voiceover_ru):
        raise RuntimeError('Provider receipt does not prove a new correlated clip')
    path=OUTPUT/Path(data['video_file']).relative_to('/data/output')
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=data['sha256']:
        raise RuntimeError('Provider video differs from its durable receipt')
    return path,data


def holds(update=None):
    path=DATA/'media_provider_holds.json'
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        values=json.loads(path.read_text()) if path.exists() else {}
        if update:
            name,value=update;values[name]=value;save(path,values)
        return values


def generate_video_file(prompt,title,run_id,resume=False,voiceover_ru=None,providers=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',run_id):raise ValueError('Invalid scene request id')
    # Continue requests created before the pool was introduced without submitting a replacement.
    if (OUTPUT/f'flow_{run_id}.json').exists():
        from autonomous_video_pipeline import generate_flow_video_file
        return generate_flow_video_file(prompt,title,run_id,resume=resume,voiceover_ru=voiceover_ru)
    folder=OUTPUT/'generation_routes';folder.mkdir(exist_ok=True)
    path=folder/(run_id+'.json');digest=hashlib.sha256(prompt.encode()).hexdigest()
    config=DATA/'media_generation_providers.json'
    providers=providers or (json.loads(config.read_text()) if config.exists() else DEFAULTS)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        state=json.loads(path.read_text()) if path.exists() else {
            'run_id':run_id,'prompt_sha256':digest,'voiceover_ru':voiceover_ru,'attempts':[]}
        if state['prompt_sha256']!=digest or state.get('voiceover_ru')!=voiceover_ru:
            raise RuntimeError('Scene route belongs to different instructions')
        if state.get('status')=='completed':return artifact(state['result'],prompt,voiceover_ru)
        providers=state.setdefault('providers',providers)
        active=next((a for a in reversed(state['attempts']) if a['status']=='active'),None)
        if active and time.time()-active['started_at']>RETAINED_MAX_SECONDS:
            owner=next((c for c in providers if c['id']==active['provider_id']),None)
            old_receipt=OUTPUT/f"{owner['provider']}_{active['request_id']}.json" if owner else None
            finished=old_receipt and old_receipt.exists() and json.loads(old_receipt.read_text()).get('status')=='completed'
            if not finished:  # a finished clip is collected by the normal path below, never abandoned
                active.update(status='abandoned_unresolved',abandoned_at=time.time())
                save(path,state);active=None
                print(json.dumps({'event':'abandoned_unresolved_request','run_id':run_id,'provider':owner and owner['id']}),flush=True)
        if active:providers=sorted(providers,key=lambda p:p['id']!=active['provider_id'])
        for cfg in providers:
            if voiceover_ru and cfg['provider']!='flow':continue
            attempt=active if active and active['provider_id']==cfg['id'] else None
            hold=holds().get(cfg['id'],{})
            if not attempt and hold.get('retry_after',0)>time.time():continue
            if not attempt:
                number=1+sum(a['provider_id']==cfg['id'] for a in state['attempts'])
                attempt={'provider_id':cfg['id'],'request_id':f'{run_id}_{cfg["id"]}_{number}',
                         'status':'active','started_at':time.time()}
                state['attempts'].append(attempt);save(path,state)
            prefix=cfg['provider'];request_id=attempt['request_id']
            receipt=OUTPUT/f'{prefix}_{request_id}.json'
            data=json.loads(receipt.read_text()) if receipt.exists() else None
            rejected=data and data.get('failure_kind') in SAFE_FAILURES and (
                not (data.get('submitted_at') or data.get('submission_attempted_at')) or data.get('terminal_failure_observed'))
            if not data or (data.get('status')!='completed' and not rejected):
                command=['docker','exec','video_factory_auth','python3','-u',f'/app/scripts/{prefix}_automator.py',prompt,title,'--run-id',request_id]
                if prefix=='flow':
                    command+=['--project-url',cfg['project_url']]
                    if voiceover_ru:command+=['--speech-ru',voiceover_ru,'--model','Veo 3.1 - Fast']
                    if data:
                        if data.get('submitted_at') or data.get('submission_attempted_at'):
                            command.append('--resume-download')
                        else:
                            # Explicit pre-submission failures are safe to retry with a fresh receipt.
                            attempt.update(status='aborted_before_submission',error=data.get('error'));save(path,state)
                            raise BeforeSubmissionFailure('Provider failed before submission; retained for the next tick')
                else:command+=['--account',str(cfg.get('account',1))]
                result=run_browser(command,capture_output=True,text=True,timeout=900)
                if not receipt.exists():raise RuntimeError('Provider produced no receipt: '+result.stderr[-400:])
                data=json.loads(receipt.read_text())
            if data.get('status')=='completed':
                value=artifact(data,prompt,voiceover_ru)
                attempt['status']='completed';state.update(status='completed',result=data);save(path,state)
                return value
            kind=data.get('failure_kind')
            attempted=bool(data.get('submitted_at') or data.get('submission_attempted_at'))
            if kind in SAFE_FAILURES and (not attempted or data.get('terminal_failure_observed')):
                delay=86400 if kind=='unsupported_country' else (300 if kind=='generation_failed' else 3600)
                attempt.update(status='unavailable',reason=kind);save(path,state)
                holds((cfg['id'],{'reason':kind,'observed_at':time.time(),'retry_after':time.time()+delay}))
                active=None
                continue
            if not attempted:
                attempt.update(status='aborted_before_submission');save(path,state)
                raise BeforeSubmissionFailure('Video provider failed before submission: '+data.get('error','unknown error')[-500:])
            save(path,state)
            raise RuntimeError('Video provider request retained; no fallback after an uncertain result: '+data.get('error','unknown error')[-500:])
        now=time.time();availability=holds()
        details={p['id']:availability.get(p['id'],{}) for p in providers if not voiceover_ru or p['provider']=='flow'}
        deadlines=[v['retry_after'] for v in details.values() if v.get('retry_after',0)>now]
        retry_after=min(deadlines) if deadlines else now+900
        state.update(status='waiting_for_included_provider',checked_at=now,
            availability=details,retry_after=retry_after);save(path,state)
        raise IncludedProvidersUnavailable(details,retry_after)
