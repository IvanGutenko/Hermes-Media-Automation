import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import growth_scout as scout
import growth_strategy as strategy
import lvl_posts
import lvl_worker
import browser_tabs


class ScoutEvidence(unittest.TestCase):
    def setUp(self):
        self.rows=[{'video_id':'one','channel_id':'a','url':'https://youtu.be/one','views':20000},
                   {'video_id':'two','channel_id':'b','url':'https://youtu.be/two','views':30000}]
        self.idea={'name_ru':'Проверяемая идея','mechanic_en':'An original visual contrast',
            'original_angle_en':'A newly written everyday conflict','query_ru':'внимание привычки',
            'query_en':'attention habit','rubric':'brain','example_ids':['one','two']}

    def test_a_direction_requires_real_examples_from_two_independent_channels(self):
        self.assertEqual(len(scout.validate_ideas({'ideas':[self.idea]},'lvl',self.rows)),1)
        self.assertEqual(scout.validate_ideas({'ideas':[{**self.idea,'example_ids':['one','invented']}]},'lvl',self.rows),[])
        rows=[{**r,'channel_id':'same'} for r in self.rows]
        self.assertEqual(scout.validate_ideas({'ideas':[self.idea]},'lvl',rows),[])
        self.assertEqual(scout.validate_ideas({'ideas':[self.idea]},'lvl',[{**r,'views':9} for r in self.rows]),[])

    def test_public_metadata_never_becomes_a_claim_that_the_source_story_was_watched(self):
        value=scout.validate_ideas({'ideas':[self.idea]},'lvl',self.rows)[0]
        self.assertFalse(value['visual_similarity_verified'])
        self.assertEqual(value['status'],'editorial_hypothesis')

    def test_an_empty_idea_query_falls_back_to_broad_seeds_in_the_same_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);now=time.time()
            old={**self.idea,'id':'x','query_ru':'очень длинный узкий запрос из двенадцати слов про скрытые расходы экспатов',
                'examples':self.rows}
            calls=[]
            def search(session,queries,at):
                calls.append(queries)
                return ([] if queries['en']!=scout.SEEDS['lvl']['en'][0] else
                    [{**r,'title':'t'} for r in self.rows]),{}
            with patch.object(scout,'BASE',root),patch.object(scout,'ROOT',root/'data/scout'),\
                    patch.object(scout,'public_videos',side_effect=search):
                scout.save(root/'data/media_brands.json',{'lvl':{}})
                scout.save(root/'data/scout/lvl.json',{'ideas':[old]})
                scout.save(root/'data/scout/attempts.json',{'lvl':{'round':1,'retry_at':0}})
                result=scout.tick(session=object(),proposer=lambda b,rows:([{'id':'new'}],'m'),now=now,only_brand='lvl')
            self.assertEqual(result['lvl']['status'],'completed')
            self.assertEqual(len(calls),2)
            self.assertLessEqual(len(calls[0]['ru'].split()),5)
            self.assertEqual(calls[1],{lg:qs[0] for lg,qs in scout.SEEDS['lvl'].items()})


class Trajectory(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=time.time()
        for name,path in (('BASE',self.root),('STATE',self.root/'data/strategy.json'),
                ('SCOUT',self.root/'data/scout'),('ASSIGNMENTS',self.root/'data/assignments.jsonl'),
                ('EVENTS',self.root/'data/events.jsonl')):
            p=patch.object(strategy,name,path);p.start();self.addCleanup(p.stop)
        self.idea={'id':'idea1','name_ru':'Направление один','mechanic_en':'Concrete contrast',
            'original_angle_en':'A new question','query_ru':'внимание привычки',
            'query_en':'attention habit','examples':[{'url':'https://youtu.be/a','channel_id':'a','views':20000},
                {'url':'https://youtu.be/b','channel_id':'b','views':30000}]}
        scout.save(strategy.SCOUT/'wrongdev.json',{'updated_at':self.now,'ideas':[self.idea,{**self.idea,'id':'idea2'}]})
        strategy.refresh(self.now,notify=False)
        self.trial=next(t for t in scout.load(strategy.STATE)['brands']['wrongdev']['trials'] if t['scope']=='youtube|video|')

    def row(self,run,views,**extra):
        return {'brand':'wrongdev','platform':'youtube','kind':'video','lang':'','run_id':run,
            'url':'https://youtu.be/'+run,'published_at':self.now-25*3600,'age_hours':25,
            'metrics':{'views':views},**extra}

    def assignments(self,n):
        return [{'brand':'wrongdev','run_id':'test'+str(i),'trial_id':self.trial['id'],'cell':'youtube|video|'} for i in range(n)]

    def test_one_weak_video_and_missing_counters_cannot_pivot_a_direction(self):
        rows=[self.row('base'+str(i),100) for i in range(3)]+[self.row('test0',1)]
        result=strategy.evaluate(self.trial,rows,self.assignments(1))['youtube|video|']
        self.assertEqual(result['decision'],'collecting')
        self.assertEqual(result['n'],1)

    def test_three_mature_weak_results_pivot_and_better_results_develop(self):
        base=[self.row('base'+str(i),100) for i in range(3)]
        weak=base+[self.row('test'+str(i),10) for i in range(3)]
        self.assertEqual(strategy.evaluate(self.trial,weak,self.assignments(3))['youtube|video|']['decision'],'pivot')
        strong=base+[self.row('test'+str(i),200) for i in range(3)]
        self.assertEqual(strategy.evaluate(self.trial,strong,self.assignments(3))['youtube|video|']['decision'],'develop')

    def test_a_telegram_post_or_another_language_never_inflates_the_video_trial(self):
        rows=[self.row('test'+str(i),1,platform='telegram',kind='post') for i in range(3)]
        self.assertEqual(strategy.evaluate(self.trial,rows,self.assignments(3)),{})
        trial={**self.trial,'scope':'youtube|video|ru'}
        rows=[self.row('test'+str(i),1,lang='en') for i in range(3)]
        self.assertEqual(strategy.evaluate(trial,rows,self.assignments(3)),{})

    def test_a_pilot_waits_after_three_assignments_then_real_mature_failures_change_the_next_direction(self):
        receipt=strategy.context('wrongdev')['receipt']
        for i in range(3):strategy.record('wrongdev','test'+str(i),receipt)
        self.assertEqual(strategy.context('wrongdev')['receipt'],{})
        rows=[self.row('base'+str(i),100) for i in range(3)]+[self.row('test'+str(i),10) for i in range(3)]
        scout.save(self.root/'data/content_age_cohorts.json',{str(i):r for i,r in enumerate(rows)})
        result=strategy.refresh(self.now+1,notify=False)
        self.assertTrue(any(e['action']=='pivot' for e in result['changes']))
        with patch.object(strategy.time,'time',return_value=self.now+1):
            self.assertEqual(strategy.context('wrongdev')['receipt']['idea_id'],'idea2')
            # The independent text-post trial has not been retired by weak videos.
            self.assertEqual(strategy.context('wrongdev',platform='telegram',kind='post')['receipt']['idea_id'],'idea1')

    def test_stale_missing_and_duplicate_measurements_do_not_create_results(self):
        a=self.row('a',3)
        scout.save(self.root/'data/content_age_cohorts.json',{'a':a,'dup':a,'stale':self.row('s',3,metrics={'views':3,'stale':True}),
            'missing':self.row('m',0,metrics={})})
        self.assertEqual(len(strategy.cohort_rows(self.now)),1)

    def test_later_normal_publications_do_not_rewrite_the_trial_baseline(self):
        rows=[self.row('test'+str(i),10) for i in range(3)]
        rows += [self.row('future'+str(i),1,published_at=self.now+3600) for i in range(3)]
        outcome=strategy.evaluate(self.trial,rows,self.assignments(3))['youtube|video|']
        self.assertEqual(outcome['baseline_n'],0)
        self.assertEqual(outcome['decision'],'explore_next')

    def test_an_editorially_held_post_does_not_exhaust_the_pilot_quota(self):
        cell='telegram|post|'
        receipt=strategy.context('wrongdev',platform='telegram',kind='post')['receipt']
        for i in range(3):strategy.record('wrongdev','held'+str(i),receipt)
        self.assertEqual(strategy.context('wrongdev',platform='telegram',kind='post')['receipt'],{})
        scout.save(self.root/'output/lvl_posts/held0/post.json',{'status':'held_for_editorial_revision'})
        self.assertEqual(strategy.context('wrongdev',platform='telegram',kind='post')['receipt']['cell'],cell)

    def test_held_video_does_not_exhaust_the_pilot_quota(self):
        receipt=strategy.context('wrongdev')['receipt']
        for i in range(3):strategy.record('wrongdev','test'+str(i),receipt)
        self.assertEqual(strategy.context('wrongdev')['receipt'],{})
        scout.save(self.root/'output/runs/test0/manifest.json',{'status':'held_for_review'})
        self.assertEqual(strategy.context('wrongdev')['receipt']['trial_id'],self.trial['id'])

    def test_refresh_cannot_make_old_demand_fresh_again(self):
        scout.save(strategy.SCOUT/'wrongdev.json',{'updated_at':self.now-8*86400,'ideas':[self.idea]})
        strategy.refresh(self.now+1,notify=False)
        self.assertEqual(strategy.context('wrongdev')['receipt'],{})
        self.assertEqual(strategy.topic_hint('wrongdev'),{})

    def test_post_topic_selection_uses_its_own_direction_instead_of_the_video_trial(self):
        state=scout.load(strategy.STATE)
        post=next(t for t in state['brands']['wrongdev']['trials'] if t['scope']=='telegram|post|')
        post['idea']={**post['idea'],'query_ru':'терминал токены расходы','query_en':'terminal tokens billing'}
        scout.save(strategy.STATE,state)
        candidate={'title':'Терминал: куда уходят токены и расходы'}
        self.assertEqual(strategy.ranking_bonus('wrongdev',candidate),1)
        self.assertEqual(strategy.ranking_bonus('wrongdev',candidate,'telegram','post',''),1.6)


class ExclusivePosts(unittest.TestCase):
    def test_exclusive_post_feedback_keeps_the_approved_episode_topic(self):
        import growth_actions as ga
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            scout.save(root/'output/lvl_runs/episode/script.json',{'topic':{'rubric':'money'}})
            scout.save(root/'output/lvl_posts/post/post.json',{'source_episode':'episode'})
            with patch.object(ga,'BASE_DIR',root):
                self.assertEqual(ga.classify('lvl',{'kind':'post','run_id':'post'}),'money')
    def test_internal_claim_ids_and_biological_scores_cannot_reach_public_text(self):
        post={'title':'Practice','body':'Write down your trigger and a possible alternative. '*10,
            'format':'worksheet','exclusive_value':'An original reusable observation worksheet','source_claim_ids':[]}
        with self.assertRaisesRegex(ValueError,'internal claim ids'):
            lvl_posts.validate({**post,'body':post['body']+' c1'},{'claims':[{'id':'c1'}]},'en')
        with self.assertRaisesRegex(ValueError,'biological game scores'):
            lvl_posts.validate({**post,'body':post['body']+' Dopamine +20'},{},'en')

    def test_unsent_cached_old_policy_is_held_and_never_sent(self):
        import content_distribution as cd
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);posts=root/'output/lvl_posts';state=root/'data/state.json'
            scout.save(root/'data/media_brands.json',{'lvl':{'publishing':{'telegram':
                {'enabled':True,'content_mode':'exclusive_posts','post_interval_seconds':7200}}}})
            scout.save(root/'output/lvl_runs/source/script.json',{'episode':{}})
            pending=posts/'cached/post.json'
            scout.save(pending,{'status':'ready','source_episode':'source','lang':'en',
                'title':'Practice','body':'Write down your trigger and a possible alternative. '*10,
                'format':'worksheet','exclusive_value':'An original reusable observation worksheet'})
            with patch.object(lvl_posts,'BASE',root),patch.object(lvl_posts,'ROOT',posts),patch.object(lvl_posts,'STATE',state),\
                    patch.object(cd,'publish',side_effect=AssertionError('old policy must not be sent')):
                result=lvl_posts.tick(now=time.time())
            self.assertEqual(result['status'],'held_for_editorial_revision')
            self.assertEqual(scout.load(pending)['status'],'held_for_editorial_revision')
    def test_live_telegram_video_guard_prevents_a_legacy_worker_from_posting_the_same_master(self):
        import content_distribution as cd
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'brands.json';p.write_text(json.dumps({'lvl':{'publishing':{'telegram':{'enabled':True,'video_enabled':False}}}}))
            with patch.object(cd,'CONFIG',p),patch.object(cd,'telegram',side_effect=AssertionError('must not send a video')):
                result=cd.publish('lvl','telegram',{'source_id':'lvl-run-ru','video':'/tmp/short.mp4'})
            self.assertEqual(result['status'],'disabled_content_kind')

    def test_telegram_exclusive_mode_removes_only_telegram_from_video_distribution(self):
        cfg={'publishing':{'telegram':{'enabled':True,'video_enabled':False},'youtube':{'enabled':True},'instagram':{'enabled':True}}}
        self.assertEqual(lvl_worker.enabled_platforms(cfg),['youtube','instagram'])

    def test_reused_caption_and_external_video_referral_are_rejected(self):
        body='A specific useful exercise with an observable record. '*10
        post={'title':'Practice','body':body,'format':'worksheet','exclusive_value':'An original reusable decision worksheet','source_claim_ids':[]}
        episode={'title_en':'Practice','description_en':body,'scenes':[]}
        with self.assertRaisesRegex(ValueError,'repeats'):lvl_posts.validate(post,episode,'en')
        with self.assertRaisesRegex(ValueError,'video referral'):
            lvl_posts.validate({**post,'body':body+' https://youtube.com/a'},{},'en')


class BrowserBootstrap(unittest.TestCase):
    def test_a_delayed_close_is_verified_by_a_later_enumeration(self):
        pages=[{'id':'one','type':'page','url':'https://www.youtube.com/account'},
            {'id':'two','type':'page','url':'https://myaccount.google.com/'}]
        pending=False;reads=0
        def fetch(path):
            nonlocal pending,reads
            if path.startswith('/json/close/'):
                pending=True;return 'Target is closing'
            if pending:
                reads+=1
                if reads>=2:pages[:]=[p for p in pages if p['id']!='one']
            return json.dumps(pages)
        report=browser_tabs.http_tidy(fetch=fetch,owner=False)
        self.assertEqual(report['closed_count'],1)
        self.assertEqual(report['after_pages'],1)

    def test_http_recovery_proves_targets_disappeared_and_keeps_auth_flow_and_pending_generation(self):
        pages=[{'id':'yt','type':'page','url':'https://www.youtube.com/watch?v=old'},
            {'id':'auth','type':'page','url':'https://accounts.google.com/v3/signin/challenge'},
            {'id':'flow','type':'page','url':'https://flow.google.com/u/0/project/pending'},
            {'id':'pics','type':'page','url':'https://docs.google.com/images/d/pending/edit'}]
        def fetch(path):
            if path=='/json/list':return json.dumps(pages)
            identity=path.rsplit('/',1)[-1];pages[:]=[p for p in pages if p['id']!=identity]
            return 'Target is closing'
        report=browser_tabs.http_tidy(fetch=fetch,owner=False)
        self.assertEqual(report['closed_count'],1)
        self.assertEqual(report['after_pages'],3)
        self.assertEqual({p['id'] for p in pages},{'auth','flow','pics'})

    def test_an_acknowledgement_without_target_disappearance_is_not_counted_as_cleanup(self):
        pages=[{'id':'one','type':'page','url':'https://www.youtube.com/'},
            {'id':'two','type':'page','url':'https://www.youtube.com/'},
            {'id':'auth','type':'page','url':'https://accounts.google.com/'}]
        report=browser_tabs.http_tidy(fetch=lambda p:json.dumps(pages) if p=='/json/list' else 'Target is closing',owner=False)
        self.assertEqual(report['closed_count'],0)
        self.assertEqual(report['after_pages'],3)

    def test_http_recovery_does_not_touch_the_connected_owners_desktop(self):
        def forbidden(_):raise AssertionError('must not call Chrome')
        self.assertEqual(browser_tabs.http_tidy(fetch=forbidden,owner=True)['closed_count'],0)

    def test_uncertain_social_uploads_protect_their_site(self):
        self.assertFalse(browser_tabs.disposable('https://www.instagram.com/',protected_sites={'instagram.com'}))


if __name__=='__main__':unittest.main()
