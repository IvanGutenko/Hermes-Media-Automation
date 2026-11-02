"""Prepare images for the real editorial queues independently of video failures."""
import fcntl
import json
import sqlite3
import os
import time
import requests
from bs4 import BeautifulSoup
from script_generator import BASE_DIR,DB_PATH
from script_generator import PROXIES
from post_images import ensure_image,cached_image
from youtube_uploader import save


def enrich_requested_posts():
    """Attach media to specifically queued text posts; preserve owner-added photos."""
    path=BASE_DIR/'data/post_image_enrichment_jobs.json'
    if not path.exists():return
    from migratoria_editor import db
    from content_distribution import migration_payload
    jobs=json.loads(path.read_text());config=json.loads((BASE_DIR/'data/media_brands.json').read_text())
    for job in jobs:
        if job.get('status') in ('completed','preserved_owner_photo'):continue
        if job['brand']!='migratoria':raise ValueError('Unexpected image enrichment brand')
        with db() as con:row=con.execute('SELECT * FROM posts WHERE id=?',(job['post_id'],)).fetchone()
        if not row or row['telegram_id']!=job['message_id']:raise ValueError('Enrichment post identity differs from database')
        cfg=config['migratoria']['publishing']['telegram'];handle=cfg['target'].lstrip('@')
        payload=migration_payload(row)
        image=ensure_image('migratoria',payload['source_id'],payload['title'],payload['text'])
        public=requests.get('https://t.me/'+handle+'/'+str(job['message_id'])+'?embed=1',proxies=PROXIES,timeout=30)
        soup=BeautifulSoup(public.text,'html.parser');node=soup.select_one('.tgme_widget_message[data-post="'+handle+'/'+str(job['message_id'])+'"]')
        if not node:raise RuntimeError('Public Telegram target cannot be read before adding an image')
        text=node.select_one('.tgme_widget_message_text')
        if not text or ' '.join(text.get_text(' ').split())!=' '.join(BeautifulSoup(payload['text'],'html.parser').get_text().split()):
            raise RuntimeError('Owner edited the text; existing post preserved')
        if node.select_one('.tgme_widget_message_photo_wrap'):
            job.update(status='preserved_owner_photo',verified_at=time.time());save(path,jobs);continue
        job.update(status='editing',image=image,requested_at=time.time());save(path,jobs)
        media={'type':'photo','media':'attach://image','caption':payload['text']}
        if payload.get('parse_mode'):media['parse_mode']=payload['parse_mode']
        try:
            with open(image,'rb') as stream:
                response=requests.post('https://api.telegram.org/bot'+os.environ[cfg['token_env']]+'/editMessageMedia',
                    data={'chat_id':cfg['target'],'message_id':job['message_id'],'media':json.dumps(media)},
                    files={'image':('image.jpg',stream,'image/jpeg')},proxies=PROXIES,timeout=90)
        except requests.RequestException:raise RuntimeError('Telegram media edit response uncertain; existing message will be reconciled') from None
        result=response.json()
        if not result.get('ok') or not result.get('result',{}).get('photo'):raise RuntimeError('Telegram did not confirm attaching the image')
        message=result['result']
        if message['message_id']!=job['message_id'] or message.get('caption')!=BeautifulSoup(payload['text'],'html.parser').get_text():
            raise RuntimeError('Telegram media edit differs from the expected post')
        job.update(status='completed',verified_at=time.time(),url='https://t.me/'+handle+'/'+str(job['message_id']))
        save(path,jobs);print(json.dumps({'enriched_post':job['url'],'photo_sizes':len(message['photo'])}),flush=True)


def tick():
    from migratoria_editor import db
    with (BASE_DIR/'data/.post-images-worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        enrich_requested_posts()
        with sqlite3.connect(DB_PATH) as con:
            con.row_factory=sqlite3.Row
            rows=[dict(r) for r in con.execute("SELECT id,title,processed_text AS text,media_urls FROM posts WHERE status='pending' ORDER BY id LIMIT 3")]
        with db() as con:
            migration=[dict(r) for r in con.execute("SELECT id,title,text FROM posts WHERE state='ready' AND telegram_id IS NULL ORDER BY id LIMIT 2")]
        for brand,rows,prefix in (('wrongdev',rows,'post-'),('migratoria',migration,'migration-post-')):
            for row in rows:
                media=row.get('media_urls')
                if media and json.loads(media):continue
                source=prefix+str(row['id'])
                if cached_image(brand,source):continue
                print(json.dumps({'brand':brand,'source_id':source,'image':ensure_image(brand,source,row['title'],row['text'])}),flush=True)
                break


if __name__=='__main__':tick()
