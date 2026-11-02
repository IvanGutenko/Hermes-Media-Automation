"""Owner reports: silent unless enabled, never repeated, never lost to a short outage."""
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import owner_notify as o


class SendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'data').mkdir()
        self.patches = [patch.object(o, 'BASE_DIR', self.root), patch.object(o, 'DATA', self.root / 'data'),
                        patch.object(o, 'SETTINGS', self.root / 'data/owner_notify.json'),
                        patch.object(o, 'STATE', self.root / 'data/owner_notify_state.json')]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def enable(self):
        o.SETTINGS.write_text(json.dumps({'enabled': True}))

    def test_nothing_is_sent_unless_the_production_flag_file_enables_it(self):
        with patch.object(o, '_deliver', return_value=True) as deliver:
            self.assertFalse(o.send('hello'))
            o.published('wrongdev', 'Title', 'https://youtu.be/x', 1)
        deliver.assert_not_called()

    def test_the_same_condition_is_not_repeated_inside_its_cooldown(self):
        self.enable()
        with patch.object(o, '_deliver', return_value=True) as deliver:
            self.assertTrue(o.send('late', key='overdue:wrongdev', cooldown=3600))
            self.assertFalse(o.send('late', key='overdue:wrongdev', cooldown=3600))
        self.assertEqual(deliver.call_count, 1)

    def test_an_unreachable_telegram_costs_one_attempt_and_the_report_is_kept_for_the_next_call(self):
        self.enable()
        with patch.object(o, '_deliver', return_value=False) as deliver:
            o.send('first')
            o.send('second')
        self.assertEqual(deliver.call_count, 2)  # one attempt per call, not one per queued message
        self.assertEqual(json.loads(o.STATE.read_text())['outbox'], ['first', 'second'])
        with patch.object(o, '_deliver', return_value=True) as deliver:
            self.assertTrue(o.send('third'))
        self.assertEqual([c.args[0] for c in deliver.call_args_list], ['first', 'second', 'third'])
        self.assertEqual(json.loads(o.STATE.read_text())['outbox'], [])

    def test_a_broken_state_file_can_never_raise_into_the_publisher(self):
        self.enable()
        o.STATE.write_text('{not json')
        with patch.object(o, '_deliver', side_effect=RuntimeError('boom')):
            self.assertFalse(o.send('x'))

    def test_published_report_names_title_link_and_next_slot_in_moscow_time(self):
        with patch.object(o, 'send') as send:
            o.published('wrongdev', 'Claude <Code>', 'https://youtu.be/abc', 1793350038)
        text = send.call_args.args[0]
        self.assertIn('WrongDev', text)
        self.assertIn('Claude &lt;Code&gt;', text)
        self.assertIn('https://youtu.be/abc', text)
        self.assertIn('11:47 МСК', text)  # 08:47 UTC

    def test_overdue_reminder_waits_for_two_hours_and_repeats_at_most_every_four(self):
        with patch.object(o, 'send') as send:
            o.overdue([{'brand': 'wrongdev', 'issue': 'publication_overdue', 'seconds': 3600}])
            send.assert_not_called()
            o.overdue([{'brand': 'wrongdev', 'issue': 'publication_overdue', 'seconds': 36000, 'error': 'quota'}])
        self.assertEqual(send.call_args.kwargs, {'key': 'overdue:wrongdev', 'cooldown': 4 * 3600})
        self.assertIn('10 ч', send.call_args.args[0])


class PlatformReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'data').mkdir()
        self.patches = [patch.object(o, 'BASE_DIR', self.root), patch.object(o, 'DATA', self.root / 'data'),
                        patch.object(o, 'SETTINGS', self.root / 'data/owner_notify.json'),
                        patch.object(o, 'STATE', self.root / 'data/owner_notify_state.json'),
                        patch.object(o, 'EVENTS', self.root / 'data/publication_events.json'),
                        patch.object(o, 'LOG', self.root / 'data/publication_log.jsonl'),
                        patch.object(o, 'AUTOPOSTER_DB', self.root / 'missing.db')]
        for p in self.patches:p.start()
        o.SETTINGS.write_text(json.dumps({'enabled': True}))

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def test_every_platform_publication_is_logged_once_and_rolled_up_in_one_message(self):
        self.assertTrue(o.publication('lvl', 'instagram', 'video', 'Почему ты устал', 'https://instagram.com/reel/a/', 'ru'))
        self.assertFalse(o.publication('lvl', 'instagram', 'video', 'Почему ты устал', 'https://instagram.com/reel/a/', 'ru'))  # same url
        o.publication('lvl', 'tiktok', 'video', 'Why tired', 'https://tiktok.com/@x/video/1', 'en')
        with patch.object(o, '_deliver', return_value=True) as deliver:
            self.assertFalse(o.flush_events())            # younger than 15 minutes and fewer than six
            self.assertTrue(o.flush_events(force=True))
        text = deliver.call_args.args[0]
        self.assertEqual(deliver.call_count, 1)
        self.assertIn('Опубликовано (2)', text)
        self.assertIn('Instagram видео [RU]', text)
        self.assertIn('TikTok видео [EN]', text)
        self.assertEqual(json.loads(o.EVENTS.read_text()), [])
        self.assertEqual(len(o.LOG.read_text().splitlines()), 2)  # the permanent log is kept

    def test_an_old_pending_entry_is_sent_without_force(self):
        o.publication('wrongdev', 'vk', 'post', 'T', 'https://vk.ru/wall-1_2')
        entries = json.loads(o.EVENTS.read_text())
        entries[0]['t'] -= 1000
        o.EVENTS.write_text(json.dumps(entries))
        with patch.object(o, '_deliver', return_value=True):
            self.assertTrue(o.flush_events())

    def test_a_failed_roll_up_is_kept_for_the_next_tick(self):
        o.publication('wrongdev', 'vk', 'post', 'T', 'https://vk.ru/wall-1_3')
        with patch.object(o, '_deliver', return_value=False):
            self.assertFalse(o.flush_events(force=True))
        self.assertEqual(len(json.loads(o.EVENTS.read_text())), 1)

    def test_a_failure_names_brand_and_platform_hides_secrets_and_repeats_at_most_every_six_hours(self):
        with patch.object(o, 'send') as send:
            o.failure('lvl', 'telegram', 'Traceback (most recent call last):\n  File "x.py", line 3\nRuntimeError: bad '
                      'https://api.telegram.org/bot9236501010:AAFXIoz_lkIYTIp1kQPRr2vtgZSaIISgegY/sendVideo failed')
        text = send.call_args.args[0]
        self.assertIn('LVL', text);self.assertIn('Telegram', text)
        self.assertNotIn('AAFXIoz', text);self.assertNotIn('9236501010', text)
        self.assertEqual(send.call_args.kwargs, {'key': 'fail:lvl:telegram', 'cooldown': 6 * 3600})

    def test_a_lost_login_is_called_a_login_problem(self):
        with patch.object(o, 'send') as send:
            o.failure('wrongdev', 'instagram', 'NeedsLogin: handle wrongdevp is not in the account list')
        self.assertIn('нужен вход', send.call_args.args[0])

    def test_health_problems_that_need_the_owner_are_reported_and_routine_retries_are_not(self):
        with patch.object(o, 'send') as send:
            o.problems([{'issue': 'research_tasks_retrying', 'tasks': ['x']}, {'issue': 'analytics_incomplete', 'targets': ['browser']}])
            send.assert_not_called()
            o.problems([{'issue': 'tiktok_publication_unverified', 'source_id': 'video-1', 'reason': 'x'},
                        {'issue': 'platform_moderation_hold', 'brand': 'wrongdev', 'platform': 'reddit', 'reason': 'removed'},
                        {'issue': 'browser_attachment_unavailable', 'consecutive_failures': 1},
                        {'issue': 'browser_attachment_unavailable', 'consecutive_failures': 3}])
        self.assertEqual(send.call_count, 3)

    def test_three_failed_attempts_in_a_row_are_reported_but_not_one(self):
        with patch.object(o, 'send') as send:
            o.retrying('lvl', 2, 'e')
            send.assert_not_called()
            o.retrying('lvl', 3, 'e')
        self.assertEqual(send.call_args.kwargs['key'], 'retry:lvl')

    def test_wrongdev_telegram_posts_are_picked_up_from_the_autoposter_database(self):
        db = self.root / 'auto.db'
        with sqlite3.connect(db) as con:
            con.execute('CREATE TABLE posts(status TEXT, title TEXT, target_msg_id INTEGER)')
            con.execute("INSERT INTO posts VALUES('posted','old',5)")
        with patch.object(o, 'AUTOPOSTER_DB', db):
            self.assertEqual(o._channel_events(), 0)      # first run only records the cursor
            with sqlite3.connect(db) as con:
                con.execute("INSERT INTO posts VALUES('posted','Новый пост',6)")
                con.execute("INSERT INTO posts VALUES('skipped','x',7)")
            self.assertEqual(o._channel_events(), 1)
            self.assertEqual(o._channel_events(), 0)
        self.assertIn('https://t.me/WrongDevp/6', o.LOG.read_text())

    def test_brand_labels_come_from_the_brand_configuration(self):
        (self.root / 'data/media_brands.json').write_text(json.dumps({'lvl': {'label': 'LVL · Прокачка'}}))
        self.assertEqual(o.label('lvl'), 'LVL · Прокачка')
        self.assertEqual(o.label('wrongdev'), 'WrongDev')


class DigestTests(unittest.TestCase):
    def test_digest_reports_only_measured_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'
            data.mkdir()
            run = root / 'output/runs/job1'
            run.mkdir(parents=True)
            (run / 'manifest.json').write_text(json.dumps({'status': 'published', 'brand': 'wrongdev', 'title': 'Заголовок',
                                                            'youtube_url': 'https://youtu.be/abc'}))
            old = root / 'output/runs/old'
            old.mkdir()
            (old / 'manifest.json').write_text(json.dumps({'status': 'published', 'brand': 'migratoria', 'title': 'Вчера'}))
            stale = time.time() - 3 * 86400
            import os
            os.utime(old / 'manifest.json', (stale, stale))
            now = time.time()
            (data / 'media_schedule.json').write_text(json.dumps({'wrongdev': {'next_due': now - 7 * 3600}, 'migratoria': {'next_due': now + 3600}}))
            (data / 'media_health.json').write_text(json.dumps({'problems': [
                {'brand': 'wrongdev', 'issue': 'publication_overdue', 'error': 'quota'},
                {'issue': 'research_tasks_retrying'}]}))
            (data / 'review_held_video_sources.json').write_text(json.dumps({'wrongdev': {'1': {}, '2': {}}}))
            with patch.object(o, 'BASE_DIR', root), patch.object(o, 'DATA', data), \
                    patch.object(o, '_channel_posts', return_value={'wrongdev': 3, 'migratoria': 0}):
                text = o.digest()
        self.assertIn('WrongDev: 1 опубликовано · слот просрочен на 7 ч', text)
        self.assertIn('Мигратория: 0 опубликовано', text)
        self.assertIn('Заголовок', text)
        self.assertNotIn('Вчера', text)  # outside the 24 h window
        self.assertIn('WrongDev: 3', text)
        self.assertIn('проблем: 1', text)  # the routine retry notice is not a problem
        self.assertIn('отложено источников на ручную проверку: 2', text)

    def test_channel_post_counts_use_the_autoposter_and_migratoria_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            with sqlite3.connect(root / 'auto.db') as con:
                con.execute('CREATE TABLE posts(status TEXT, posted_date TEXT)')
                con.execute("INSERT INTO posts VALUES('posted','2999-01-01T00:00:00')")
                con.execute("INSERT INTO posts VALUES('posted','2000-01-01T00:00:00')")
                con.execute("INSERT INTO posts VALUES('skipped_ads','2999-01-01T00:00:00')")
            with sqlite3.connect(root / 'data/migratoria.db') as con:
                con.execute('CREATE TABLE posts(telegram_id INTEGER, created_at REAL)')
                con.execute('INSERT INTO posts VALUES(5, ?)', (time.time(),))
                con.execute('INSERT INTO posts VALUES(NULL, ?)', (time.time(),))
            with patch.object(o, 'AUTOPOSTER_DB', root / 'auto.db'), patch.object(o, 'DATA', root / 'data'):
                self.assertEqual(o._channel_posts(time.time() - 3600), {'wrongdev': 1, 'migratoria': 1})


if __name__ == '__main__':unittest.main()
