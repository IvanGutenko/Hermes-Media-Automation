import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import growth_actions as ga
import growth_brief as gb
import growth_engine as ge


class Actions(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root = Path(temp.name);self.data = self.root/'data';self.data.mkdir()
        self.now = time.time()
        for module, name, value in ((ga,'BASE_DIR',self.root),(ga,'DATA',self.data),(ga,'PATH',self.data/'growth_actions.json'),
                (ga,'LOG',self.data/'consumption.jsonl'),(gb,'PATH',self.data/'brief.json'),(gb,'ASSIGNMENTS',self.data/'assign.jsonl')):
            p=patch.object(module,name,value);p.start();self.addCleanup(p.stop)
        self.write('growth_report.json', {'updated_at':self.now,'brands':{},'experiments':[]})

    def write(self, name, data):
        (self.data/name).write_text(json.dumps(data))

    def row(self, title, views, url, **extra):
        return {'brand':'wrongdev','platform':'youtube','kind':'video','url':url,'title':title,
            'published_at':self.now-25*3600,'age_hours':25,'metrics':{'views':views},**extra}

    def themes(self):
        rows={}
        for n in range(4):
            rows[f'h{n}']=self.row('Утечка данных',500+n,f'h{n}')
            rows[f'l{n}']=self.row('Подписка дорожает',5+n,f'l{n}')
        self.write('content_age_cohorts.json',rows)
        return rows

    def test_only_meaningful_policy_changes_notify_the_owner(self):
        old={'status':'active','brands':{'wrongdev':{'actions':[], 'allocations':{}}}}
        plan={'actions':[], 'allocations':{'telegram|post|':{'topics':{
            'pricing_limits':{'n':4,'weight':.75,'median_views':2}}}}}
        current={'brands':{'wrongdev':plan}}
        with patch('owner_notify.send') as send:
            ga.notify_decisions(old,current)
            self.assertEqual(send.call_count,1)
            self.assertIn('Снизил',send.call_args.args[0])
            self.assertIn('Telegram',send.call_args.args[0])
            send.reset_mock()
            unchanged={'status':'active',**current}
            newer=json.loads(json.dumps(current));newer['brands']['wrongdev']['allocations']['telegram|post|']['topics']['pricing_limits']['n']=5
            ga.notify_decisions(unchanged,newer)
            send.assert_not_called()

    def test_same_age_topics_get_bounded_bias_and_twenty_percent_exploration(self):
        self.themes();state=ga.refresh(self.now)
        cell=state['brands']['wrongdev']['allocations']['youtube|video|']['topics']
        self.assertGreater(cell['incident']['weight'],cell['pricing_limits']['weight'])
        self.assertEqual(cell['incident']['n'],4)
        self.assertTrue(all(.75<=x['weight']<=1.35 for x in cell.values()))
        self.assertEqual(state['exploration_fraction'],.2)

    def test_repeated_snapshots_and_other_platforms_do_not_inflate_a_group(self):
        rows=self.themes();rows['again']=rows['h0'];rows['ig']=self.row('Утечка данных',9999,'ig',platform='instagram')
        self.write('content_age_cohorts.json',rows);state=ga.refresh(self.now)
        self.assertEqual(state['brands']['wrongdev']['allocations']['youtube|video|']['topics']['incident']['n'],4)
        self.assertEqual(state['brands']['wrongdev']['allocations']['instagram|video|']['topics']['incident']['weight'],1)

    def test_missing_stale_and_wrong_age_observations_never_become_zeroes(self):
        rows={'missing':self.row('Утечка',None,'a'),'old':self.row('Утечка',100,'b',age_hours=75),
            'stale':self.row('Утечка',100,'c',metrics={'views':100,'stale':True})}
        self.write('content_age_cohorts.json',rows)
        self.assertEqual(ga.refresh(self.now)['brands']['wrongdev']['allocations'],{})

    def test_single_viral_outlier_never_changes_topic_weight(self):
        self.write('content_age_cohorts.json',{'a':self.row('Утечка',99999999,'a')})
        topic=ga.refresh(self.now)['brands']['wrongdev']['allocations']['youtube|video|']['topics']['incident']
        self.assertEqual((topic['weight'],topic['status']),(1,'single_no_bias'))

    def test_stale_measurements_disable_action_consumption_and_do_not_refresh_them(self):
        self.themes();ga.refresh(self.now)
        with patch.object(ga.time,'time',return_value=self.now+86401):
            self.assertEqual(ga.context('wrongdev')['receipt'],{})
            self.assertIsNone(ga.choose('wrongdev',[{'id':1}]))
            self.assertEqual(ga.refresh()['status'],'waiting_for_fresh_measurements')
            self.assertEqual(ga.status()['effective_status'],'waiting_for_fresh_measurements')

    def test_live_selector_records_the_real_candidate_chosen_with_measured_weights(self):
        self.themes();state=ga.refresh(self.now)
        candidates=[{'id':1,'title':'Подписка дорожает'},{'id':2,'title':'Утечка данных'}]
        rng=unittest.mock.Mock();rng.random.return_value=.9;rng.choices.return_value=[candidates[1]]
        self.assertEqual(ga.choose('wrongdev',candidates,rng=rng)['id'],2)
        entry=json.loads(ga.LOG.read_text().splitlines()[-1])
        self.assertEqual((entry['source_id'],entry['revision'],entry['mode']),(2,state['revision'],'measured_topic_allocation'))
        self.assertGreater(rng.choices.call_args.kwargs['weights'][1],rng.choices.call_args.kwargs['weights'][0])

    def test_exploration_can_choose_a_weaker_topic_without_removing_it(self):
        self.themes();ga.refresh(self.now)
        candidates=[{'id':1,'title':'Подписка дорожает'},{'id':2,'title':'Утечка данных'}]
        rng=unittest.mock.Mock();rng.random.return_value=.1;rng.choice.return_value=candidates[0]
        self.assertEqual(ga.choose('wrongdev',candidates,rng=rng)['id'],1)
        self.assertEqual(json.loads(ga.LOG.read_text().splitlines()[-1])['mode'],'exploration')

    def test_lack_of_comparable_topic_data_preserves_existing_source_order(self):
        ga.refresh(self.now)
        self.assertEqual(ga.choose('wrongdev',[{'id':1},{'id':2}])['id'],1)

    def action_report(self):
        videos=[{'url':str(n),'age_hours':100,'avg_pct':40,'diagnosis':[{'stage':'viewers_leave_early'}]} for n in range(3)]
        self.write('growth_report.json',{'updated_at':self.now,'brands':{'lvl':{'videos':videos}}})

    def test_lvl_gets_a_concrete_action_and_new_experiments_protect_their_factor(self):
        self.action_report();ga.refresh(self.now)
        self.assertIn('first two scenes',ga.context('lvl')['prompt'])
        self.assertEqual(ga.context('lvl')['receipt']['actions'],['early_payoff'])
        with patch.object(gb,'active',return_value=[{'factor':'angle'}]):
            self.assertEqual(ga.context('lvl')['receipt']['actions'],[])

    def test_payoff_timing_also_protects_an_existing_hook_experiment(self):
        self.action_report();ga.refresh(self.now)
        with patch.object(gb,'active',return_value=[{'factor':'hook'}]):
            self.assertEqual(ga.context('lvl')['receipt']['actions'],[])
            self.assertEqual(ga.refresh(self.now)['brands']['lvl']['actions'],[])

    def test_lvl_script_features_use_the_actual_rubric_instead_of_other(self):
        run=self.root/'output/lvl_runs/lvl-one';run.mkdir(parents=True)
        saved={'topic':{'rubric':'brain'},'episode':{'scenes':[{},{}],'growth_actions':{'revision':'abc'}}}
        (run/'script.json').write_text(json.dumps(saved))
        self.assertEqual(ga.classify('lvl',{'run_id':'lvl-one'}),'brain')
        self.assertEqual(ge.script_features(run,'lvl')['topic'],'brain')
        self.assertEqual(ge.script_features(run,'lvl')['growth_actions'],{'revision':'abc'})

    def test_lvl_sparse_data_keeps_rubric_diversity_and_does_not_pool_languages(self):
        ga.refresh(self.now)
        self.assertEqual(ga.choose_rubric({'body':3,'brain':0,'money':2}),'brain')
        entry=json.loads(ga.LOG.read_text().splitlines()[-1])
        self.assertEqual(entry['mode'],'diversity_insufficient_comparable_data')

    def test_low_lvl_retention_is_a_hypothesis_even_when_the_whole_series_has_a_low_median(self):
        videos=[{'url':str(n),'age_hours':100,'avg_pct':40,'lang':lang,'diagnosis':[]}
            for n,lang in enumerate(('en','en','ru','ru','ru'))]
        self.write('growth_report.json',{'updated_at':self.now,'brands':{'lvl':{'videos':videos}}})
        action=ga.refresh(self.now)['brands']['lvl']['actions'][0]
        self.assertEqual((action['status'],action['n'],action['lang']),('hypothesis',3,'ru'))

    def test_status_distinguishes_assigned_from_mature_samples(self):
        self.write('growth_report.json',{'experiments':[{'id':'e','n_test':0,'n_control':0}]})
        gb.ASSIGNMENTS.write_text(json.dumps({'experiment':'e','arm':'test'})+'\n')
        progress=ga.status()['experiments'][0]
        self.assertEqual((progress['assigned_test'],progress['n_test']),(1,0))


if __name__=='__main__':unittest.main()
