#!/usr/bin/env python3
"""Independent research/distribution tick so an unavailable platform cannot stop video generation."""
import fcntl
import json
import time
from migratoria_editor import db,collect,prepare_one,BASE_DIR
from content_distribution import migration_post
from article_factory import prepare_article

if __name__=='__main__':
    with (BASE_DIR/'data/.research.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit(0)
        tasks=[('telegram',migration_post),('collect',collect)]
        with db() as con:ready=con.execute("SELECT count(*) FROM posts WHERE state='ready'").fetchone()[0]
        if ready<2:tasks.append(('editor',prepare_one))
        tasks.append(('dzen_article',prepare_article))
        statuses={}
        errors=False
        for name,fn in tasks:
            try:
                fn();statuses[name]={'status':'completed','checked_at':time.time()}
            except Exception as exc:
                errors=True
                statuses[name]={'status':'retry_on_next_tick','error':type(exc).__name__+': '+str(exc)[-400:],'checked_at':time.time()}
                print(json.dumps({'task':name,**statuses[name]},ensure_ascii=False),flush=True)
        from youtube_uploader import save
        save(BASE_DIR/'data/research_status.json',statuses)
        migration_post()
        raise SystemExit(1 if errors else 0)
