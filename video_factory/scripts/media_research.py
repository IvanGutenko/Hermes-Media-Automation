#!/usr/bin/env python3
"""Recurring editorial work, with durable progress and bounded independent tasks."""
import fcntl
import json
import signal
import subprocess
import sys
import time
from functools import partial
from migratoria_editor import db, collect, prepare_one, BASE_DIR
from content_distribution import migration_post
from article_factory import prepare_article
from youtube_uploader import save


# Measured 2026-11-08: one candidate takes 85-440 s, so 300 s killed most runs
# before a single post was prepared. New candidates start only within the budget.
EDITOR_BUDGET = 540
EDITOR_TIMEOUT = 900
editor = partial(prepare_one, budget=EDITOR_BUDGET)


def tasks():
    from growth_scout import tick as scout
    from growth_strategy import refresh as strategy
    from lvl_posts import tick as exclusive_posts
    # Browser/image work must not prevent decisions or text-only posts.
    work = [('demand_scout', scout, 240), ('editorial_strategy', strategy, 60),
            ('growmode_exclusive_posts', exclusive_posts, 180),
            ('telegram', migration_post, 420), ('collect', collect, 300)]
    with db() as con:
        ready = con.execute("SELECT count(*) FROM posts WHERE state='ready'").fetchone()[0]
    if ready < 2:work.append(('editor', editor, EDITOR_TIMEOUT))
    work.append(('dzen_article', prepare_article, 300))
    return work


def execute(name, timeout):
    # Terminate a stalled task's process group, retaining its publication intents
    # for normal reconciliation. A timeout never authorizes a second send.
    process = subprocess.Popen([sys.executable, '-u', __file__, '--task', name], start_new_session=True)
    try:return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        import os
        os.killpg(process.pid, signal.SIGTERM)
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL);process.wait()
        return 124


def main():
    path = BASE_DIR/'data/research_status.json'
    if len(sys.argv) == 3 and sys.argv[1] == '--task':
        name = sys.argv[2]
        fn = dict((key, fn) for key, fn, _ in tasks()).get(name)
        if fn is None:
            if name == 'editor':fn = editor
            else:raise ValueError('Unknown research task')
        try:
            result = fn()
            status = {'status':'completed','checked_at':time.time()}
            if isinstance(result, dict):
                status['result'] = result
                if name == 'demand_scout' and any(isinstance(v,dict) and v.get('status')=='retry' for v in result.values()):
                    status['status'] = 'retry_on_next_tick'
        except Exception as exc:
            status = {'status':'retry_on_next_tick','error':type(exc).__name__+': '+str(exc)[-400:],'checked_at':time.time()}
        # Only this parent runs tasks, one at a time; the existing lock spans all.
        statuses = json.loads(path.read_text()) if path.exists() else {}
        statuses[name] = status;save(path, statuses)
        logged = {k:v for k,v in status.items() if k!='result'}
        if isinstance(status.get('result'),dict):
            logged['result'] = {k:v for k,v in status['result'].items() if k!='brands'}
        print(json.dumps({'task':name, **logged},ensure_ascii=False), flush=True)
        return int(status['status'] != 'completed')
    with (BASE_DIR/'data/.research.lock').open('a') as lock:
        try:fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        errors = False
        for name, _, timeout in tasks():
            statuses = json.loads(path.read_text()) if path.exists() else {}
            statuses[name] = {'status':'running','started_at':time.time(),'checked_at':time.time()}
            save(path, statuses)
            code = execute(name, timeout)
            if code:
                errors = True
                if code == 124 or json.loads(path.read_text()).get(name,{}).get('status') == 'running':
                    statuses = json.loads(path.read_text())
                    statuses[name] = {'status':'retry_on_next_tick','error':'Task exceeded its execution limit' if code==124 else 'Task process failed', 'checked_at':time.time()}
                    save(path, statuses)
        return int(errors)


if __name__ == '__main__':raise SystemExit(main())
