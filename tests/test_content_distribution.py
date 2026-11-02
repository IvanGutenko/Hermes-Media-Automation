import hashlib,json,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import content_distribution as distribution
import script_generator


class PendingPublicationTests(unittest.TestCase):
    def test_native_reddit_success_survives_a_lost_worker_response_and_preserves_profile_cadence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'output';out.mkdir();data=root/'data';data.mkdir()
            config=data/'brands.json';config.write_text(json.dumps({'migratoria':{'publishing':{'reddit':{'enabled':True,'target':'u_ThinkScope'}}}}))
            payload={'source_id':'migration-post-13','title':'Reviewed title','text':'Reviewed body'}
            key=hashlib.sha256(json.dumps({'brand':'migratoria','target':'u_ThinkScope','source':payload['source_id'],'kind':'post'},sort_keys=True).encode()).hexdigest()
            job={'key':key,'username':'ThinkScope','source_id':payload['source_id'],'title':payload['title'],'body':payload['text'],'sha256':None}
            (out/('reddit_native_job_'+key+'.json')).write_text(json.dumps(job))
            proof={**job,'status':'published','id':'realid','url':'https://www.reddit.com/user/ThinkScope/comments/realid/',
                'publication_attempted_at':100,'public_verified_at':200}
            (out/('native_reddit_'+key+'.json')).write_text(json.dumps(proof))
            result=Mock(returncode=0,stdout=json.dumps({'status':'published','url':proof['url']}))
            with patch.object(distribution,'BASE_DIR',root),patch.object(distribution,'DATA',data),patch.object(distribution,'CONFIG',config),patch.object(distribution,'run_browser',return_value=result),patch.object(distribution.time,'time',return_value=300):
                published=distribution.publish('migratoria','reddit',payload)
                scheduled=distribution.publish('migratoria','reddit',{**payload,'source_id':'migration-post-14'})
            self.assertEqual(published['public_verified_at'],200)
            self.assertEqual(scheduled['status'],'scheduled')
            self.assertEqual(json.loads((data/'distribution/reddit_ThinkScope_cursor.json').read_text())['last_post_at'],100)

    def test_vk_native_send_state_survives_publisher_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'output';out.mkdir();image=out/'image.jpg';image.write_bytes(b'photo')
            state={'key':'b'*64,'status':'new','source_id':'post-45'}
            payload={'image':str(image),'text':'Reviewed caption','source_id':'post-45'}
            native={**state,'status':'publishing','sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'text':payload['text']}
            (out/('native_vk_photo_'+state['key']+'.json')).write_text(json.dumps(native))
            persisted=[]
            with patch.object(distribution,'BASE_DIR',root),patch.object(distribution.subprocess,'run',side_effect=subprocess.TimeoutExpired('publisher',360)):
                with self.assertRaises(subprocess.TimeoutExpired):distribution.vk({'group_id':1},payload,state,lambda:persisted.append(dict(state)))
            self.assertEqual(persisted[0]['status'],'preparing')
            self.assertEqual(persisted[-1]['status'],'publishing')

    def test_old_reconciliation_never_resets_a_newer_reddit_daily_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cursor.json'
            distribution.update_reddit_cursor(path,'new-video',200,'new-url')
            distribution.update_reddit_cursor(path,'old-video',100,'old-url')
            distribution.update_reddit_cursor(path,'new-video',200,'new-url')
            state=json.loads(path.read_text())
            self.assertEqual(state,{'source_id':'new-video','last_post_at':200,'url':'new-url'})
    def test_newer_source_cannot_bypass_unverified_wall_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';receipts=data/'distribution';receipts.mkdir(parents=True)
            config=data/'brands.json';config.write_text(json.dumps({'wrongdev':{'publishing':{'vk':{'enabled':True}}}}))
            db=root/'posts.db'
            with sqlite3.connect(db) as con:
                con.execute('CREATE TABLE posts(id INTEGER,title TEXT,processed_text TEXT,posted_date TEXT,status TEXT)')
                con.executemany('INSERT INTO posts VALUES(?,?,?,?,?)',[(45,'Older sent post','sent text','today','posted'),(46,'Newer source','new text','today','posted')])
            (receipts/('wrongdev_vk_'+'a'*64+'.json')).write_text(json.dumps({'key':'a'*64,'status':'posted_unverified','source_id':'post-45','id':7}))
            (receipts/'wrongdev_vk_post_cursor.json').write_text(json.dumps({'last_id':40,'last_post_at':1000000}))
            with patch.object(distribution,'BASE_DIR',root),patch.object(distribution,'DATA',data),patch.object(distribution,'CONFIG',config),patch.object(script_generator,'DB_PATH',db),patch.object(distribution.time,'time',return_value=1000001),patch.object(distribution,'publish',return_value={'status':'published','url':'https://vk.ru/wall-1_7'}) as publish:
                distribution.distribute_posts()
            self.assertEqual(publish.call_args.args[2]['source_id'],'post-45')
            self.assertEqual(json.loads((receipts/'wrongdev_vk_post_cursor.json').read_text())['last_id'],45)


    def test_a_post_unverifiable_for_twelve_hours_stops_blocking_the_queue_and_is_never_resent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';receipts=data/'distribution';receipts.mkdir(parents=True)
            config=data/'brands.json';config.write_text(json.dumps({'wrongdev':{'publishing':{'vk':{'enabled':True}}}}))
            db=root/'posts.db'
            with sqlite3.connect(db) as con:
                con.execute('CREATE TABLE posts(id INTEGER,title TEXT,processed_text TEXT,posted_date TEXT,status TEXT)')
                con.executemany('INSERT INTO posts VALUES(?,?,?,?,?)',[(70,'Stuck','t','today','posted'),(71,'Next','new text','today','posted')])
            stuck=receipts/('wrongdev_vk_'+'a'*64+'.json')
            stuck.write_text(json.dumps({'key':'a'*64,'status':'publishing','source_id':'post-70','publish_requested_at':1000}))
            (receipts/'wrongdev_vk_post_cursor.json').write_text(json.dumps({'last_id':67,'last_post_at':0}))
            now=1000+13*3600
            with patch.object(distribution,'BASE_DIR',root),patch.object(distribution,'DATA',data),patch.object(distribution,'CONFIG',config),patch.object(script_generator,'DB_PATH',db),patch.object(distribution.time,'time',return_value=now),patch.object(distribution,'add_post_image',side_effect=lambda brand,payload,media=None:payload),patch.object(distribution,'publish',return_value={'status':'published','url':'https://vk.ru/wall-1_9'}) as publish:
                distribution.distribute_posts()
            self.assertEqual(publish.call_args.args[2]['source_id'],'post-71')
            self.assertEqual(json.loads(stuck.read_text())['status'],'abandoned_unverified')
            self.assertEqual(json.loads((receipts/'wrongdev_vk_post_cursor.json').read_text())['last_id'],71)

    def test_a_receipt_without_a_timestamp_gets_its_clock_started_not_abandoned(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt=Path(tmp)/'r.json';value={'status':'posted_unverified','source_id':'post-45'};receipt.write_text(json.dumps(value))
            self.assertFalse(distribution.abandon_if_stale(receipt,value,Path(tmp)/'c.json',{'last_id':1},'wrongdev','vk',now=5000))
            self.assertEqual(json.loads(receipt.read_text())['first_pending_at'],5000)


if __name__=='__main__':unittest.main()
