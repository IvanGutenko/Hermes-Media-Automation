"""An uncertain send is never repeated and cannot stop unrelated reviewed topics."""
import json,os,sqlite3,sys,tempfile,time,types,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import content_distribution as d


def public_message(text,post=59,photo=True):
    date=datetime.fromtimestamp(1000,timezone.utc).isoformat()
    return f'<div class="tgme_widget_message" data-post="Own/{post}"><time datetime="{date}"></time><div class="tgme_widget_message_text">{text}</div>'+('<a class="tgme_widget_message_photo_wrap"></a>' if photo else '')+'</div>'


class TelegramReadback(unittest.TestCase):
    cfg={'token_env':'TEST_TELEGRAM_TOKEN','target':'@Own'}
    def chat(self):
        response=Mock(ok=True);response.json.return_value={'ok':True,'result':{'id':42,'type':'channel','username':'Own'}}
        return response

    def test_inline_links_and_breaks_do_not_change_the_source_text(self):
        doc=public_message('Первый <a href="https://example.org">точный текст</a>.<br><br>Второй абзац.')
        state={'sent_plain':'Первый точный текст.\n\nВторой абзац.','send_requested_at':999}
        self.assertEqual(d.telegram_readback(doc,{'image':'own.jpg'},state)[0]['id'],59)

    def test_changed_number_missing_photo_and_old_copy_are_not_adopted(self):
        state={'sent_plain':'Сто рублей','send_requested_at':999}
        for doc,override in [(public_message('Двести рублей'),{}),(public_message('Сто рублей',photo=False),{}),(public_message('Сто рублей'),{'send_requested_at':2000})]:
            with self.subTest(doc=doc):self.assertEqual(d.telegram_readback(doc,{'image':'own.jpg'},{**state,**override}),[])

    def test_ambiguous_copy_remains_pending_without_another_send(self):
        page=Mock(text=public_message('Точный текст',59)+public_message('Точный текст',60),status_code=200)
        state={'status':'sending','sent_plain':'Точный текст','send_requested_at':999}
        with patch.dict(os.environ,TEST_TELEGRAM_TOKEN='test'),patch.object(d.requests,'post',return_value=self.chat()) as post,patch.object(d.requests,'get',return_value=page):
            with self.assertRaises(d.TelegramSendUncertain):d.telegram(self.cfg,{'image':'own.jpg'},state,lambda:None)
        self.assertEqual(post.call_count,1)
        self.assertTrue(post.call_args.args[0].endswith('/getChat'))
        self.assertEqual(state['status'],'sending')

    def test_lost_response_is_reconciled_to_one_existing_message(self):
        page=Mock(text=public_message('Точный текст'),status_code=200)
        state={'status':'sending','sent_plain':'Точный текст','send_requested_at':999}
        with patch.dict(os.environ,TEST_TELEGRAM_TOKEN='test'),patch.object(d.requests,'post',return_value=self.chat()) as post,patch.object(d.requests,'get',return_value=page):
            actual=d.telegram(self.cfg,{'image':'own.jpg'},state,lambda:None)
        self.assertEqual(actual['id'],59);self.assertEqual(actual['status'],'published')
        self.assertEqual(actual['public_published_at'],1000);self.assertEqual(post.call_count,1)

    def test_network_failure_after_intent_retains_the_original_request(self):
        state={'status':'new'}
        with patch.dict(os.environ,TEST_TELEGRAM_TOKEN='test'),patch.object(d.requests,'post',side_effect=[self.chat(),d.requests.exceptions.ReadTimeout()]):
            with self.assertRaises(d.TelegramSendUncertain):d.telegram(self.cfg,{'text':'Точный текст'},state,lambda:None)
        self.assertEqual(state['status'],'sending');self.assertEqual(state['sent_plain'],'Точный текст')
        self.assertGreater(state['send_requested_at'],0)


class MigratoriaQueue(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.data=self.root/'data';self.data.mkdir()
        self.cfg={'publishing':{'telegram':{'enabled':True,'target':'@Own'}}}
        self.config=self.data/'brands.json';self.config.write_text(json.dumps({'migratoria':self.cfg}))
        self.database=self.data/'posts.db';self.now=20000
        with self.db() as con:
            con.execute('CREATE TABLE posts (id INTEGER PRIMARY KEY,title TEXT,text TEXT,evidence TEXT,state TEXT,telegram_id INTEGER,published_at REAL,created_at REAL)')
            for i in (1,2):con.execute('INSERT INTO posts VALUES (?,?,?,?,?,NULL,NULL,?)',(i,'Тема '+str(i),'Текст '+str(i),'{"documents":[]}','ready',self.now-10))
        self.patches=[patch.object(d,'DATA',self.data),patch.object(d,'CONFIG',self.config),patch.object(d.time,'time',return_value=self.now),patch('migratoria_editor.db',side_effect=self.db),patch.object(d,'add_post_image',side_effect=lambda brand,payload:payload)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def db(self):
        con=sqlite3.connect(self.database);con.row_factory=sqlite3.Row;return con
    def receipt(self,at):
        path,_=d.receipt_of('migratoria','telegram',self.cfg['publishing']['telegram'],'migration-post-1','post')
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'status':'sending','source_id':'migration-post-1','send_requested_at':at}))
    def send(self,brand,platform,payload):
        if payload['source_id']=='migration-post-1':raise d.TelegramSendUncertain('lost response')
        return {'status':'published','id':60,'url':'https://t.me/Own/60','public_published_at':self.now}
    def rows(self):
        with self.db() as con:return [dict(r) for r in con.execute('SELECT * FROM posts ORDER BY id')]
    def test_old_uncertain_source_keeps_its_receipt_while_the_next_topic_publishes(self):
        self.receipt(self.now-10801)
        with patch.object(d,'publish',side_effect=self.send) as publish:
            d.migration_post();d.migration_post()
        rows=self.rows();self.assertEqual(rows[0]['state'],'telegram_pending');self.assertIsNone(rows[0]['telegram_id'])
        self.assertEqual(rows[1]['state'],'posted');self.assertEqual(rows[1]['telegram_id'],60)
        self.assertEqual([c.args[2]['source_id'] for c in publish.call_args_list],['migration-post-1','migration-post-1','migration-post-2'])
    def test_recent_unknown_send_preserves_the_three_hour_spacing(self):
        self.receipt(self.now-10)
        with patch.object(d,'publish',side_effect=self.send) as publish:
            d.migration_post();d.migration_post()
        self.assertEqual(self.rows()[1]['state'],'ready')
        self.assertNotIn('migration-post-2',[c.args[2]['source_id'] for c in publish.call_args_list])


if __name__=='__main__':unittest.main()
