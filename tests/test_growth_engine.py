import json,os,random,sys,tempfile,time,unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import growth_brief as gb
import growth_engine as ge
import script_generator as sg

def experiment(**extra):
    base={'id':'closing-short','brand':'wrongdev','hypothesis':'h','change':'c','metric':'m','evidence':'e','min_samples':8,
        'review_by':(date.today()+timedelta(days=30)).isoformat(),'factor':'closing','metric_key':'engaged_views','params':{'closing':'short'}}
    return {**base,**extra}

def rows(test,control,exp='closing-short',key='engaged_views',age=100):
    out=[]
    for arm,values in (('test',test),('control',control)):
        for v in values:out.append({key:v,'age_hours':age,'experiments':{exp:arm}})
    return out

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.dir=Path(self.tmp.name)
        for target,attr,name in ((gb,'PATH','growth_brief.json'),(gb,'RULES_PATH','growth_rules.json'),(gb,'ASSIGNMENTS','assign.jsonl'),(ge,'STORE','store.json'),(ge,'REPORT','report.json')):
            p=patch.object(target,attr,self.dir/name);p.start();self.addCleanup(p.stop)
        p=patch.object(ge,'BASE_DIR',self.dir/'factory');p.start();self.addCleanup(p.stop)

class Judge(Base):
    def test_a_clear_win_is_adopted_and_a_clear_loss_rejected(self):
        win=ge.judge(experiment(),rows([100,120,130,140,150,160,170,180],[10,20,30,40,50,60,70,80]))
        self.assertEqual(win['decision'],'adopt');self.assertLessEqual(win['p'],0.2);self.assertGreaterEqual(win['ratio'],1.25)
        self.assertEqual(ge.judge(experiment(),rows([10,20,30,40,50,60,70,80],[100,120,130,140,150,160,170,180]))['decision'],'reject')
    def test_too_few_videos_decide_nothing_however_large_the_gap(self):
        self.assertEqual(ge.judge(experiment(),rows([1000]*7,[1]*8))['decision'],'collecting')
    def test_one_outlier_does_not_win(self):
        result=ge.judge(experiment(),rows([1,2,3,4,5,6,7,100000],[1,2,3,4,5,6,7,8]))
        self.assertEqual(result['decision'],'collecting')
    def test_young_videos_do_not_count(self):
        result=ge.judge(experiment(),rows([100]*8,[10]*8,age=30))
        self.assertEqual((result['n_test'],result['decision']),(0,'collecting'))
    def test_missing_metric_is_absent_not_zero(self):
        data=rows([100]*8,[10]*8);data.append({'age_hours':100,'experiments':{'closing-short':'test'},'engaged_views':None})
        self.assertEqual(ge.judge(experiment(),data)['n_test'],8)
    def test_a_long_tie_closes_as_no_effect_and_an_old_one_expires(self):
        same=[10,20,30,40,50,60,70,80]*3
        self.assertEqual(ge.judge(experiment(),rows(same,same))['decision'],'no_effect')
        old=experiment(review_by='2020-01-01')
        self.assertEqual(ge.judge(old,rows([1],[1]))['decision'],'expired')

class Close(Base):
    def test_an_adopted_change_becomes_a_rule_and_the_experiment_ends(self):
        gb.save({'experiments':[experiment()]})
        data={'wrongdev':rows([100,120,130,140,150,160,170,180],[10,20,30,40,50,60,70,80])}
        told=[]
        ge.close_experiments(data,notify=lambda e,r:told.append(r['decision']))
        self.assertEqual(told,['adopt']);self.assertEqual(json.loads(gb.PATH.read_text())['experiments'],[])
        fields=gb.assign('wrongdev','run-later')
        self.assertEqual(fields['fields']['closing'],'short');self.assertIn('c',fields['prompt'])
    def test_a_rejected_change_leaves_no_rule(self):
        gb.save({'experiments':[experiment()]})
        ge.close_experiments({'wrongdev':rows([10,20,30,40,50,60,70,80],[100,120,130,140,150,160,170,180])},notify=lambda e,r:None)
        self.assertEqual(gb.rules('wrongdev'),[])

class Assign(Base):
    def test_arms_stay_balanced_stable_and_recorded(self):
        gb.save({'experiments':[experiment()]})
        arms=[gb.assign('wrongdev',f'run{i}')['fields']['experiments']['closing-short'] for i in range(20)]
        self.assertEqual(arms.count('test'),10)
        self.assertEqual(gb.assign('wrongdev','run3')['fields']['experiments']['closing-short'],arms[3])
        self.assertEqual(len(gb.ASSIGNMENTS.read_text().splitlines()),20)
    def test_only_the_test_arm_gets_the_change(self):
        gb.save({'experiments':[experiment(change='SHORT CHANGE')]})
        seen={}
        for i in range(4):
            a=gb.assign('wrongdev',f'r{i}');seen[a['fields']['experiments']['closing-short']]=a
        self.assertIn('SHORT CHANGE',seen['test']['prompt']);self.assertEqual(seen['test']['fields']['closing'],'short')
        self.assertEqual(seen['control']['prompt'],'');self.assertNotIn('closing',seen['control']['fields'])
    def test_other_brand_is_untouched(self):
        gb.save({'experiments':[experiment()]})
        self.assertEqual(gb.assign('migratoria','r'),{'prompt':'','fields':{}})
    def test_post_writers_do_not_see_video_experiments(self):
        gb.save({'experiments':[experiment()]})
        self.assertEqual(gb.prompt('wrongdev'),'')
    def test_add_keeps_running_experiments_and_refuses_a_rival_on_one_factor(self):
        gb.save({'experiments':[experiment()]})
        gb.add({'experiments':[experiment(id='hook-stakes',factor='hook',params={})]})
        self.assertEqual(len(json.loads(gb.PATH.read_text())['experiments']),2)
        with self.assertRaises(ValueError):gb.add({'experiments':[experiment(id='closing-two')]})
    def test_validation_refuses_unknown_factor_metric_and_free_params(self):
        for bad in ({'factor':'publishing'},{'metric_key':'revenue'},{'params':{'voice':'x'}}):
            with self.assertRaises(ValueError):gb.validate({'experiments':[experiment(**bad)]})

class Closing(unittest.TestCase):
    def script(self,**extra):
        scene=lambda text:{'voiceover_ru':text,'video_prompt_en':'A specific close shot of a laptop on a wooden desk with a blank screen','on_screen_text_ru':'Текст','edit_note_ru':'Склейка'}
        return {'scenes':[scene('Первая сцена про важное событие'),scene('Вторая сцена с деталями события'),scene('Итог простыми словами')],
            'youtube_meta':{'title':'Заголовок ролика','description':'Описание','tags':['теги']},**extra}
    def test_the_short_closing_keeps_the_payoff_and_is_not_doubled(self):
        with patch.dict(os.environ,MEDIA_BRAND='wrongdev'):
            data=sg.validate_script(self.script(closing='short'))
            self.assertEqual(data['scenes'][-1]['voiceover_ru'],'Итог простыми словами Подробности в Телеграм канале')
            self.assertEqual(sg.validate_script(data)['scenes'][-1]['voiceover_ru'],'Итог простыми словами Подробности в Телеграм канале')
            self.assertIn('Telegram',data['scenes'][-1]['on_screen_text_ru'])
    def test_a_second_pitch_in_the_payoff_is_refused_so_the_model_rewrites_it(self):
        with patch.dict(os.environ,MEDIA_BRAND='wrongdev'):
            data=self.script(closing='short');data['scenes'][-1]['voiceover_ru']='Узнайте больше в описании'
            with self.assertRaises(ValueError):sg.validate_script(data)
    def test_the_question_arm_needs_a_real_question_and_a_resumed_script_still_validates(self):
        with patch.dict(os.environ,MEDIA_BRAND='wrongdev'):
            with self.assertRaises(ValueError):sg.validate_script(self.script(question=True))
            data=self.script(question=True);data['scenes'][1]['voiceover_ru']='Вы бы рискнули или нет?'
            done=sg.validate_script(data);self.assertEqual(done['question'],'checked')
            self.assertEqual(sg.validate_script(done)['question'],'checked')
    def test_a_new_payoff_leaves_room_for_the_channel_words_but_a_resumed_script_stays_valid(self):
        with patch.dict(os.environ,MEDIA_BRAND='migratoria'):
            long='Проверьте почту и активируйте приглашение до дедлайна сегодня ночью'
            data=self.script(closing='short');data['scenes'][-1]['voiceover_ru']=long
            with self.assertRaises(sg.ArmRuleError):sg.validate_script(data)
            data=self.script(closing='short')
            data['scenes'][-1]['voiceover_ru']=long+' Подробности в Телеграм канале Мигратория'
            self.assertEqual(sg.validate_script(data)['closing'],'short')
    def test_an_unmet_arm_rule_drops_the_arm_on_the_last_try_and_the_video_is_not_counted(self):
        import json
        experiments=[{'id':'wd-q','brand':'wrongdev','params':{'question':True}},{'id':'wd-hook','brand':'wrongdev'}]
        fields={'question':True,'experiments':{'wd-q':'test','wd-hook':'control'}}
        data=self.script()
        data.update(visual_mode='hybrid',motion_system='editorial-v2',motion_palette='cobalt')
        for scene,kind,layout in zip(data['scenes'],['hybrid','motion','motion'],['spotlight','workflow','kinetic']):
            scene.update(visual_type=kind,motion={'layout':layout,'headline':'Суть изменения',
                'items':['Первый','Второй'],'kicker':''})
        raw=json.dumps(data)
        with patch.dict(os.environ,MEDIA_BRAND='wrongdev'),patch.object(gb,'active',return_value=experiments):
            with self.assertRaises(sg.ArmRuleError):sg._validated(raw,fields,'wrongdev',last_try=False)
            data=sg._validated(raw,fields,'wrongdev',last_try=True)
        self.assertEqual(data['experiments'],{'wd-hook':'control'})
        self.assertNotIn('question',data)
    def test_a_question_without_the_mark_counts_because_the_prompt_forbids_punctuation(self):
        with patch.dict(os.environ,MEDIA_BRAND='wrongdev'):
            for text in ('Какая стратегия лучше для обучения','Стоит ли лететь сейчас','Вы бы рискнули или нет','Лететь сейчас или ждать'):
                data=self.script(question=True);data['scenes'][1]['voiceover_ru']=text
                self.assertEqual(sg.validate_script(data)['question'],'checked',text)
            for text in ('Структура данных важнее чем размер','Чтобы получить мили нужна карта'):
                data=self.script(question=True);data['scenes'][1]['voiceover_ru']=text
                with self.assertRaises(ValueError,msg=text):sg.validate_script(data)
    def test_without_the_field_the_fixed_closing_stays(self):
        with patch.dict(os.environ,MEDIA_BRAND='wrongdev'):
            data=sg.validate_script(self.script())
            self.assertEqual(data['scenes'][-1]['voiceover_ru'],sg.sanitize_voiceover(sg.CTA_LINKS_RU))

class Diagnose(Base):
    BASE={'median_views':1000,'median_engaged_pct':50,'median_avg_pct':80}
    def stages(self,**row):
        return [d['stage'] for d in ge.diagnose({'age_hours':100,**row},self.BASE)]
    def test_each_stage_names_where_people_were_lost(self):
        self.assertIn('not_distributed',self.stages(views=100))
        self.assertIn('weak_opening',self.stages(views=1000,engaged_pct=30))
        self.assertIn('viewers_leave_early',self.stages(views=1000,avg_pct=50))
        self.assertIn('closing_too_long',self.stages(views=1000,closing_share=0.5))
        self.assertIn('no_comments',self.stages(views=1000,comments=0))
    def test_absent_numbers_produce_no_finding(self):
        self.assertEqual(self.stages(views=1000),['ok'])
        self.assertEqual(ge.diagnose({'age_hours':10,'views':0},self.BASE)[0]['stage'],'too_early')

class Topics(Base):
    def test_classes(self):
        self.assertEqual(ge.topic_class('wrongdev','Как скрытый markdown украл ключи, бэкдор в IDE'),'incident')
        self.assertEqual(ge.topic_class('migratoria','Виза в Канаду и контроль на границе'),'visa_border')
        self.assertEqual(ge.topic_class('lvl','что угодно'),'other')
    def test_better_class_is_chosen_more_often_but_the_other_stays_in_play(self):
        history=[{'topic':'incident','views':1200,'age_hours':100}]*5+[{'topic':'pricing_limits','views':10,'age_hours':100}]*5
        a={'id':1,'title':'Тариф и лимит подписки','text':''};b={'id':2,'title':'Бэкдор и взлом','text':''}
        rng=random.Random(4);picks=[ge.thompson_pick('wrongdev',[a,b],history,rng)['id'] for _ in range(400)]
        self.assertGreater(picks.count(2),picks.count(1));self.assertGreater(picks.count(1),0)
    def test_little_history_keeps_the_newest_and_a_failure_never_stops_production(self):
        a={'id':1,'title':'a','text':''};b={'id':2,'title':'b','text':''}
        self.assertEqual(ge.thompson_pick('wrongdev',[a,b],[])['id'],1)
        with patch.object(ge,'rows_for',side_effect=RuntimeError('x')):self.assertEqual(ge.pick_source('wrongdev',[a,b])['id'],1)

class Collect(Base):
    def test_api_failure_keeps_old_numbers_and_interval_is_respected(self):
        import yt_analytics
        ge.STORE.write_text(json.dumps({'videos':{'abc':{'views':5}},'channels':{},'read_at':0}))
        with patch.object(yt_analytics,'access_token',side_effect=RuntimeError('no token')):
            store=ge.collect_api({'wrongdev':{'youtube_channel_id':'UC'}},now=1000000)
        self.assertEqual(store['videos']['abc']['views'],5);self.assertIn('wrongdev',store['errors'])
        with patch.object(yt_analytics,'access_token',side_effect=AssertionError('must not read')):
            ge.collect_api({'wrongdev':{'youtube_channel_id':'UC'}},now=1000000+60)

class Funnel(Base):
    def test_a_channel_read_failure_leaves_no_invented_point(self):
        def boom(handle):raise RuntimeError('page')
        with patch.object(ge,'DATA',self.dir):state=ge.telegram_funnel({'wrongdev':{'telegram':'@X'}},now=1000,get_count=boom)
        self.assertEqual(state['series'].get('X'),[]);self.assertIsNone(ge.funnel_summary(state,'X')['members_now'])
    def test_gain_is_read_from_two_real_observations(self):
        state={'series':{'X':[{'t':0,'members':24},{'t':7200,'members':30}]},'links':{}}
        out=ge.funnel_summary(state,'X');self.assertEqual((out['gain'],out['since_hours'],out['members_now']),(6,2.0,30))
    def test_topic_classes_after_reorder(self):
        self.assertEqual(ge.topic_class('migratoria','Барбекю в First Class, рейс из Далласа'),'premium_curiosity')
