"""Persisted balanced voice assignment; never change an in-progress take."""
import fcntl
import json
import time
from script_generator import BASE_DIR
from youtube_uploader import save


def choose(brand,run_id):
    path=BASE_DIR/'data/voice_experiments.json'
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        state=json.loads(path.read_text()) if path.exists() else {'voices':['Holt'],'assignments':{}}
        assignment=state.setdefault('assignments',{}).get(run_id)
        if assignment:return assignment['voice']
        voices=state.get('voices',['Holt'])
        counts={v:sum(1 for x in state['assignments'].values() if x['brand']==brand and x['voice']==v) for v in voices}
        voice=min(voices,key=lambda v:counts[v])
        state['assignments'][run_id]={'brand':brand,'voice':voice,'assigned_at':time.time()}
        save(path,state)
        return voice
