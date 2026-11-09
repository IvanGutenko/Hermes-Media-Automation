#!/usr/bin/env python3
"""Publish what is ready to every enabled platform, separately from research, so slow models never delay a post."""
import fcntl
import json
import os
import time
from pathlib import Path
BASE_DIR=Path(os.getenv('VIDEO_FACTORY_DIR',Path(__file__).resolve().parent.parent))

if __name__=='__main__':
    with (BASE_DIR/'data/.distribution.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit(0)
        from content_distribution import distribute_videos,distribute_posts,distribute_articles,MAX_BROWSER_PUBLICATIONS
        from youtube_uploader import save
        budget=[MAX_BROWSER_PUBLICATIONS]
        # Videos first: they carry the reach, and an account cadence shared with photo posts should go to them.
        tasks=[('video_distribution',lambda:distribute_videos(budget)),('posts_distribution',lambda:distribute_posts(budget)),
            ('dzen_publication',distribute_articles)]
        status_path=BASE_DIR/'data/distribution_status.json'
        statuses=json.loads(status_path.read_text()) if status_path.exists() else {}
        errors=False
        for name,fn in tasks:
            statuses[name]={'status':'running','started_at':time.time(),'checked_at':time.time()}
            save(status_path,statuses)
            try:
                fn();statuses[name]={'status':'completed','checked_at':time.time()}
            except Exception as exc:
                errors=True
                # The cause is at the end of a retained traceback, not at its start.
                statuses[name]={'status':'retry_on_next_tick','error':type(exc).__name__+': '+str(exc)[-400:],'checked_at':time.time()}
                print(json.dumps({'task':name,**statuses[name]},ensure_ascii=False),flush=True)
            save(status_path,statuses)
        raise SystemExit(1 if errors else 0)
