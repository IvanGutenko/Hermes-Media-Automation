import hashlib,json,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import content_distribution as distribution
import script_generator


class PendingPublicationTests(unittest.TestCase):
    def test_community_cadence_ignores_profile_and_other_community_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'output';out.mkdir()
            for i,(target,stamp) in enumerate((('u_ThinkScope',300),('r_Migratoriya',200),('r_WrongDev',100))):
                (out/('native_reddit_'+str(i)*64+'.json')).write_text(json.dumps({
                    'status':'published','username':'ThinkScope','target':target,'source_id':target,
                    'public_verified_at':stamp,'url':'https://www.reddit.com/comments/'+str(i)+'/'}))
            cursor=root/'cursor.json'
            with patch.object(distribution,'BASE_DIR',root):distribution.sync_reddit_cursor(cursor,'r_WrongDev')
            self.assertEqual(json.loads(cursor.read_text())['last_post_at'],100)
            self.assertEqual(json.loads(cursor.read_text())['source_id'],'r_WrongDev')

    def test_retired_native_adapter_preserves_existing_receipts_without_starting_a_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'output';out.mkdir()
            cfg={'target':'r_WrongDev','username':'ThinkScope'}
            payload={'source_id':'post-1','title':'Title','text':'Body'}
            state={'key':'d'*64,'source_id':'post-1'}
            proof={'status':'published','username':'ThinkScope','target':'r_Migratoriya',
                   'source_id':'post-1','sha256':None,'public_verified_at':200,'id':'wrong',
                   'url':'https://www.reddit.com/r/Migratoriya/comments/wrong/'}
            (out/('native_reddit_'+state['key']+'.json')).write_text(json.dumps(proof))
            response=Mock(returncode=0,stdout=json.dumps({'status':'published','url':proof['url']}))
            with patch.object(distribution,'BASE_DIR',root),patch.object(distribution,'run_publisher',return_value=response) as worker:
                result=distribution.reddit(cfg,payload,state,lambda:None)
            self.assertEqual(result['status'],'disabled')
            worker.assert_not_called()
            self.assertFalse((out/('reddit_native_job_'+state['key']+'.json')).exists())
            self.assertEqual(json.loads((out/('native_reddit_'+state['key']+'.json')).read_text()),proof)
            self.assertNotEqual(state.get('status'),'published')

    def test_stale_enabled_config_cannot_retry_reddit_or_create_a_new_publication(self):
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
            with patch.object(distribution,'BASE_DIR',root),patch.object(distribution,'DATA',data),patch.object(distribution,'CONFIG',config),patch.object(distribution,'run_browser',return_value=result) as worker,patch.object(distribution.time,'time',return_value=300):
                published=distribution.publish('migratoria','reddit',payload)
                scheduled=distribution.publish('migratoria','reddit',{**payload,'source_id':'migration-post-14'})
            self.assertEqual(published['status'],'disabled')
            self.assertEqual(scheduled['status'],'disabled')
            worker.assert_not_called()
            self.assertFalse((data/'distribution').exists())
            self.assertEqual(json.loads((out/('native_reddit_'+key+'.json')).read_text()),proof)

    def test_legacy_reconciliation_neither_reads_the_browser_nor_reenables_reddit(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);config=data/'media_brands.json'
            before=json.dumps({'wrongdev':{'publishing':{'reddit':{'enabled':False,'post_enabled':False,'video_enabled':False}}}})
            config.write_text(before)
            (data/'reddit_pending_jobs.json').write_text('["old-job.json"]')
            with patch.object(distribution,'DATA',data),patch.object(distribution,'CONFIG',config),patch.object(distribution,'run_publisher') as worker:
                self.assertEqual(distribution.reconcile_reddit()['status'],'disabled')
            worker.assert_not_called()
            self.assertEqual(config.read_text(),before)

    def test_posts_skip_reddit_even_with_old_enabled_flags_and_a_pending_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);receipts=data/'distribution';receipts.mkdir()
            config=data/'media_brands.json'
            config.write_text(json.dumps({'wrongdev':{'publishing':{'reddit':{'enabled':True,'post_enabled':True}}}}))
            receipt=receipts/('wrongdev_reddit_'+'a'*64+'.json')
            receipt.write_text(json.dumps({'status':'publishing','source_id':'post-1'}))
            with patch.object(distribution,'DATA',data),patch.object(distribution,'CONFIG',config),patch.object(distribution,'publish') as worker:
                distribution.distribute_posts()
            worker.assert_not_called()
            self.assertEqual(json.loads(receipt.read_text())['status'],'publishing')

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
