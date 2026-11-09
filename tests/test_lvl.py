"""LVL series: art, audio, voice helpers, topics, script validation, scheduling. Network and ffmpeg are never required."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
_tmp = tempfile.mkdtemp()
os.environ['VIDEO_FACTORY_DIR'] = _tmp
os.environ['LVL_FFMPEG_MODE'] = 'local'
import lvl_art, lvl_audio, lvl_cast, lvl_scene, lvl_script, lvl_topics, lvl_voice  # noqa: E402


def episode(ru_words=11, en_words=13, scenes=6):
    ru = ' '.join(['слово'] * ru_words)
    en = ' '.join(['word'] * en_words)
    out = []
    for i in range(scenes):
        out.append({'speaker': 'lvl', 'text_ru': ru, 'text_en': en, 'setting': 'room_night', 'claim': 'c1' if i == 2 else None,
            'actors': [{'id': 'lvl', 'side': 'center', 'expression': 'tired', 'action': 'slump'}], 'props': [{'icon': 'moon', 'x': 0.3, 'y': 0.2, 'at': 0.5}],
            'popup': '+10 XP' if i == scenes - 2 else None, 'level_up': False})
    return {'title_ru': 'Заголовок', 'title_en': 'Title', 'summary': 's', 'scenes': out, 'claims': [{'id': 'c1', 'text_en': 'claim', 'evidence': 'e1'}],
        'description_ru': 'd', 'description_en': 'd', 'hashtags_ru': ['#лвл'], 'hashtags_en': ['#lvl']}


class ArtTests(unittest.TestCase):
    def test_every_shape_and_expression_renders_a_sprite(self):
        for shape in lvl_art.SHAPES:
            spec = lvl_art.clean_spec({'shape': shape, 'accessory': 'crown'})
            im = lvl_art.draw_character(spec, {'expression': 'excited', 'mouth': 0.5}, 120)
            self.assertEqual(im.size, (120, 120))
            self.assertIsNotNone(im.getbbox())
        for expression in lvl_art.EXPRESSIONS:
            self.assertIsNotNone(lvl_art.draw_character(lvl_art.default_spec(), {'expression': expression}, 96).getbbox())

    def test_bad_specs_fall_back_to_safe_values(self):
        spec = lvl_art.clean_spec({'shape': 'dragon', 'body': 'not-a-colour', 'width': 99, 'eyes': 'laser'})
        self.assertEqual((spec['shape'], spec['eyes'], spec['width']), ('squircle', 'round', 1.35))
        self.assertEqual(spec['body'], lvl_art.default_spec()['body'])

    def test_backgrounds_and_icons_have_the_frame_size(self):
        for setting in lvl_scene.SETTINGS:
            self.assertEqual(lvl_scene.background(setting, 1).size, (1080, 1920))
        from PIL import Image
        for name in lvl_scene.ICONS:
            canvas = Image.new('RGBA', (300, 300), (0, 0, 0, 0))
            lvl_scene.draw_icon(canvas, name, 150, 150, 200)
            self.assertIsNotNone(canvas.getbbox(), name)

    def test_captions_split_into_short_phrases(self):
        line = 'Первый босс: недосып. Каждый час сна даёт энергию, а не лень'.split()
        chunks = lvl_scene.chunk_words(line)
        self.assertTrue(all(len(ws) <= 4 for _, ws in chunks))
        self.assertEqual(sum(len(ws) for _, ws in chunks), len(line))


class AudioVoiceTests(unittest.TestCase):
    def test_effects_and_music_are_generated_once(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(lvl_audio, 'AUDIO_DIR', Path(tmp)):
            path = lvl_audio.sfx('pop')
            self.assertTrue(path.exists())
            stamp = path.stat().st_mtime_ns
            self.assertEqual(lvl_audio.sfx('pop').stat().st_mtime_ns, stamp)
            with self.assertRaises(ValueError):lvl_audio.sfx('nope')

    def test_silence_is_trimmed_and_the_lead_is_reported(self):
        quiet = [0] * 22050
        loud = [3000, -3000] * 5000
        trimmed, lead = lvl_voice.trim_silence(quiet + loud + quiet)
        self.assertAlmostEqual(lead, 1.0 - 0.06, places=2)
        self.assertLess(len(trimmed), len(quiet) * 2 + len(loud))

    def test_mouth_envelope_follows_loudness(self):
        samples = [0] * 5000 + [12000, -12000] * 5000 + [0] * 5000
        env = lvl_voice.envelope(samples, fps=24)
        self.assertEqual(env[0], 0.0)
        self.assertGreater(max(env), 0.8)
        self.assertEqual(env[-1], 0.0)

    def test_speech_service_hiccups_are_retried_with_growing_pauses(self):
        import array
        calls, sleeps = [], []
        async def flaky(*args):
            calls.append(1)
            if len(calls) < 3:raise lvl_voice.VoiceError('no audio received')
            return b'x', [{'text': 'а', 'start': 0.0, 'end': 0.4}]
        loud = array.array('h', [3000] * int(lvl_voice.RATE * 1.0))
        with patch.object(lvl_voice, '_stream', flaky), patch.object(lvl_voice, 'decode_pcm', return_value=loud), patch.object(lvl_voice.time, 'sleep', sleeps.append):
            self.assertGreater(lvl_voice.speak('Привет', 'hero', 'ru', proxy='')['duration'], 0.9)
        self.assertEqual((len(calls), sleeps), (3, [2.0, 4.0]))

    def test_speech_gives_up_after_the_last_attempt_without_a_pointless_final_pause(self):
        sleeps = []
        async def dead(*args):raise lvl_voice.VoiceError('no audio received')
        with patch.object(lvl_voice, '_stream', dead), patch.object(lvl_voice.time, 'sleep', sleeps.append):
            with self.assertRaises(lvl_voice.VoiceError):lvl_voice.speak('Привет', 'hero', 'ru', proxy='')
        self.assertEqual(sleeps, [2.0, 4.0, 6.0, 8.0, 10.0, 12.0])

    def test_dmitry_no_audio_uses_a_native_russian_fallback_and_records_both_voices(self):
        import array
        NoAudioReceived=type('NoAudioReceived',(Exception,),{})
        voices=[]
        async def flaky(text,voice,*args):
            voices.append(voice)
            if voice=='ru-RU-DmitryNeural':raise NoAudioReceived()
            return b'x', []
        loud=array.array('h',[3000]*lvl_voice.RATE)
        with patch.object(lvl_voice,'_stream',flaky),patch.object(lvl_voice,'decode_pcm',return_value=loud),patch.object(lvl_voice.time,'sleep'):
            result=lvl_voice.speak('Привет','hero','ru',proxy='')
        self.assertEqual(voices,['ru-RU-DmitryNeural']*2+['ru-RU-SvetlanaNeural'])
        self.assertEqual(result['requested_voice'],'ru-RU-DmitryNeural')
        self.assertEqual(result['voice'],'ru-RU-SvetlanaNeural')
        self.assertEqual(result['fallback_reason'],'primary_voice_no_audio')

    def test_every_speaker_kind_has_a_voice_in_both_languages(self):
        for kind in lvl_voice.SPEAKERS:
            for lang in ('ru', 'en'):
                voice, pitch, rate, volume = lvl_voice.voice_for(kind, lang)
                self.assertTrue(voice.startswith(lang))


class TopicTests(unittest.TestCase):
    def test_novelty_penalises_repeats(self):
        history = [{'title_en': 'Why am I always tired after lunch', 'title_ru': ''}]
        self.assertLess(lvl_topics.novelty('why am i always tired after lunch', history), 0.2)
        self.assertEqual(lvl_topics.novelty('how to start investing', history), 1.0)

    def test_relevance_uses_query_terms(self):
        words = lvl_topics.keywords('sleep restriction, fatigue daytime')
        good = {'title': 'Sleep restriction and daytime fatigue', 'text': 'x'}
        bad = {'title': 'Airway suction guidelines', 'text': 'sleep'}
        self.assertEqual(lvl_topics.relevance(good, words), 1.0)
        self.assertLess(lvl_topics.relevance(bad, words), 0.5)

    def test_evidence_skips_failed_sources_and_irrelevant_items(self):
        item = {'source': 'PubMed', 'title': 'Sleep duration and fatigue in adults', 'year': '2024', 'journal': 'J', 'kind': 'review', 'url': 'u', 'text': 'sleep duration fatigue ' * 30}
        junk = {'source': 'PubMed', 'title': 'Unrelated suction paper', 'year': '2020', 'journal': 'J', 'kind': 'review', 'url': 'u2', 'text': 'nothing relevant here at all ' * 20}
        import requests
        def pubmed(term):return [item, junk]
        def europepmc(term):raise requests.ConnectionError()
        def medlineplus(term):return []
        def page(entry):raise requests.ConnectionError()
        with patch.object(lvl_topics, 'pubmed', pubmed), patch.object(lvl_topics, 'europepmc', europepmc), patch.object(lvl_topics, 'medlineplus', medlineplus), \
                patch.object(lvl_topics, 'page', page):
            items, errors = lvl_topics._evidence('sleep duration fatigue', 'energy', 6, 0.6)
        self.assertEqual([i['title'] for i in items], ['Sleep duration and fatigue in adults'])
        self.assertEqual(items[0]['id'], 'e1')
        self.assertIn('europepmc', errors)
        self.assertEqual(errors['curated'], 'unreachable')


class CastTests(unittest.TestCase):
    def test_a_guest_lookalike_is_refused_and_a_distinct_one_accepted(self):
        cast = lvl_cast.load()
        twin = {'id': 'twin', 'name_ru': 'Двойник', 'name_en': 'Twin', 'spec': {**cast['lvl']['spec']}}
        with self.assertRaises(ValueError):lvl_cast.add_guest(twin, cast)
        new = {'id': 'wallet', 'name_ru': 'Кошелёк', 'name_en': 'Wallet', 'voice': 'sly', 'spec': {'shape': 'capsule', 'body': '#10b981', 'accessory': 'cap'}}
        self.assertEqual(lvl_cast.add_guest(new, cast)['name_en'], 'Wallet')
        self.assertIn('wallet', cast)


class ScriptValidationTests(unittest.TestCase):
    cast = None

    def setUp(self):
        self.cast = lvl_cast.load()

    def check(self, data):
        return lvl_script.validate(data, self.cast, {'e1', 'e2'})

    def test_a_well_formed_episode_passes(self):
        out = self.check(episode())
        self.assertEqual(len(out['scenes']), 6)
        self.assertEqual(out['scenes'][0]['actors'][0]['side'], 'center')

    def rejects(self, data, message):
        with self.assertRaises(ValueError) as ctx:self.check(data)
        self.assertIn(message, str(ctx.exception))

    def test_numbers_must_be_words_in_russian(self):
        e = episode();e['scenes'][1]['text_ru'] = 'спи 8 часов каждую ночь да'
        self.rejects(e, 'numbers as words')

    def test_promises_and_advice_are_refused(self):
        e = episode();e['scenes'][1]['text_en'] = 'this guarantees you wake up fresh'
        self.rejects(e, 'forbidden')
        e = episode();e['scenes'][1]['text_ru'] = 'это вылечит усталость навсегда сразу'
        self.rejects(e, 'forbidden')

    def test_a_spoken_disclaimer_is_refused_in_both_languages_but_ordinary_speech_is_not(self):
        for lang, text in (('ru', 'это общая информация а не совет'), ('ru', 'это не медицинская и не финансовая рекомендация'), ('en', 'this is general information not advice friends'),
                           ('en', 'please consult your doctor before anything else')):
            e = episode();e['scenes'][1]['text_' + lang] = text
            self.rejects(e, 'spoken disclaimer')
        e = episode();e['scenes'][1]['text_ru'] = 'я не советую спорить с ленью по утрам без хорошего кофе';e['scenes'][1]['text_en'] = 'honestly i would not advise arguing with sloth before the first coffee of day'
        self.assertEqual(len(self.check(e)['scenes']), 6)

    def test_claims_must_cite_known_evidence_and_be_used(self):
        e = episode();e['claims'][0]['evidence'] = 'e9'
        self.rejects(e, 'unknown evidence')
        e = episode();e['scenes'][2]['claim'] = None
        self.rejects(e, 'no scene references a claim')
        e = episode();e['claims'] = []
        self.rejects(e, 'claim')

    def test_speaker_must_be_on_screen_and_length_bounded(self):
        e = episode();e['scenes'][1]['actors'] = [{'id': 'len', 'side': 'center'}]
        self.rejects(e, 'speaker must be on screen')
        self.rejects(episode(ru_words=4, en_words=4), 'total')
        self.rejects(episode(ru_words=14, en_words=14, scenes=8), 'total')
        e = episode();e['scenes'][0]['text_ru'] = ' '.join(['слово'] * 15)
        self.rejects(e, 'hook')

    def test_needs_an_xp_reward_and_two_actors_do_not_overlap(self):
        e = episode()
        for s in e['scenes']:s['popup'] = None
        self.rejects(e, 'XP popup')
        e = episode();e['scenes'][1]['actors'] = [{'id': 'lvl', 'side': 'left'}, {'id': 'len', 'side': 'left'}]
        sides = {a['id']: a['side'] for a in self.check(e)['scenes'][1]['actors']}
        self.assertEqual(sorted(sides.values()), ['left', 'right'])


class LayoutTests(unittest.TestCase):
    class FakeFont:
        def __init__(self, size):self.size = size
        def getlength(self, text):return len(text) * self.size * 0.6

    def test_end_card_lines_shrink_to_fit_the_frame(self):
        import lvl_render
        lvl_render.fit_size.cache_clear()
        with patch.object(lvl_render.lvl_art, 'font', lambda size, *a, **k: self.FakeFont(size)):
            fitted = lvl_render.fit_size('Следующий уровень скоро', 64, 'Unbounded', 900)   # 23 chars: 883 px at 64, so it fits as is
            long_line = lvl_render.fit_size('Следующий уровень скоро и ещё немного текста', 64, 'Unbounded', 900)
            self.assertEqual(fitted, 64)
            self.assertLess(long_line, 64)
            self.assertLessEqual(len('Следующий уровень скоро и ещё немного текста') * long_line * 0.6, 960)
            self.assertEqual(lvl_render.fit_size('x' * 400, 64, 'Unbounded', 900), 28)       # never below the readable minimum
        lvl_render.fit_size.cache_clear()

    def test_the_title_card_sits_above_the_faces_of_the_cast(self):
        import lvl_render
        self.assertLess(lvl_render.TITLE_Y + 45 * 2 + 90 + 60, 1000)


class TopicPickTests(unittest.TestCase):
    def pick(self, answer, exclude=()):
        pool = [{'phrase': 'how many steps a day is healthy', 'lang': 'en', 'rank': 0}, {'phrase': 'why am i always tired', 'lang': 'en', 'rank': 1}]
        seen = {}
        def fake_llm(messages, temperature=0.5):
            seen['prompt'] = messages[-1]['content']
            return answer, 'm'
        with patch.object(lvl_script.lvl_topics, 'used', return_value=[]), patch.object(lvl_script.lvl_topics, 'demand', return_value=[dict(p) for p in pool]), \
                patch.object(lvl_script.lvl_topics, 'trending', return_value=[]), patch.object(lvl_script, '_llm', fake_llm):
            return lvl_script.pick_topic('body', exclude=exclude), seen['prompt']

    def test_an_echoed_list_line_is_mapped_back_to_the_offered_phrase(self):
        base = {'title_en': 't', 'title_ru': 'т', 'evidence_query_en': 'steps'}
        for echoed in ('[en] how many steps a day is healthy (rank 0)', '- [en] How many steps a day is healthy', 'how many steps a day is healthy'):
            topic, _ = self.pick({**base, 'phrase': echoed})
            self.assertEqual(topic['phrase'], 'how many steps a day is healthy')

    def test_an_excluded_phrase_is_not_offered_again(self):
        base = {'title_en': 't', 'title_ru': 'т', 'evidence_query_en': 'tired', 'phrase': 'why am i always tired'}
        _, prompt = self.pick(base, exclude=['how many steps a day is healthy'])
        self.assertNotIn('how many steps a day is healthy', prompt.split('PHRASES:')[1].split('TRENDING')[0])
        self.assertIn('why am i always tired', prompt)


class ReviewTests(unittest.TestCase):
    """The fact check must end in a clear verdict: pass, named problems, or 'no verdict' (never an unexplained rejection)."""
    ep = {'claims': [{'id': 'c1', 'evidence': 'e1', 'text_en': 'x'}], 'scenes': [{'speaker': 'lvl', 'text_en': 'Sleep matters.'}]}
    ev = [{'id': 'e1', 'title': 't', 'source': 'PubMed', 'year': '2024', 'text': 'sleep'}]

    def run_review(self, *answers):
        replies = [({**a}, 'm') for a in answers]
        with patch.object(lvl_script, '_llm', side_effect=replies) as call:
            return lvl_script.review(self.ep, self.ev), call.call_count

    def test_clear_pass_and_clear_problems(self):
        self.assertEqual(self.run_review({'ok': True, 'problems': []})[0], (True, []))
        (ok, problems), _ = self.run_review({'ok': False, 'problems': ['number 40% is not in the source']})
        self.assertFalse(ok)
        self.assertEqual(problems, ['number 40% is not in the source'])

    def test_problems_win_even_when_ok_is_true_and_a_string_counts_as_one_problem(self):
        self.assertEqual(self.run_review({'ok': True, 'problems': 'causal claim'})[0], (False, ['causal claim']))

    def test_a_verdict_without_reasons_is_asked_again_then_reported_as_no_verdict(self):
        result, calls = self.run_review({'ok': False, 'problems': []}, {'result': 'unclear'})
        self.assertEqual((result, calls), ((None, []), 2))
        result, calls = self.run_review({'ok': False, 'problems': []}, {'ok': True, 'problems': []})
        self.assertEqual((result, calls), ((True, []), 2))

    def test_write_episode_does_not_accept_a_script_the_checker_could_not_judge(self):
        topic = {'title_en': 't', 'rubric': 'energy', 'phrase': 'p'}
        ev = [{'id': 'e1', 'title': 't', 'source': 'PubMed', 'year': '2024', 'journal': 'J', 'text': 'sleep', 'relevance': 1}]
        with patch.object(lvl_script, 'validate', return_value=self.ep), patch.object(lvl_script, '_llm', return_value=({}, 'm')), \
                patch.object(lvl_script, 'review', return_value=(None, [])), patch.object(lvl_script.lvl_topics, 'used', return_value=[]):
            with self.assertRaises(lvl_script.ScriptError) as ctx:lvl_script.write_episode(topic, ev, lvl_cast.load(), previous=[], attempts=2)
        self.assertIn('no usable verdict', str(ctx.exception))


class LlmGuardTests(unittest.TestCase):
    def test_a_hanging_model_is_abandoned_and_skipped_afterwards(self):
        import time as clock
        calls = []
        def fake_call(provider, model, messages, **kw):
            calls.append(model)
            if model == 'slow':clock.sleep(5)
            return json.dumps({'ok': 1})
        with tempfile.TemporaryDirectory() as tmp, patch.object(lvl_script, 'SLOW_FILE', Path(tmp) / 'slow.json'), patch.object(lvl_script, 'WALL_SECONDS', {'groq': 0.3}), \
                patch.object(lvl_script.text_llm, 'candidates', return_value=[('groq', 'slow'), ('groq', 'fast')]), patch.object(lvl_script.text_llm, 'call', side_effect=fake_call):
            started = clock.time()
            data, model = lvl_script._llm([])
            self.assertLess(clock.time() - started, 3)
            self.assertEqual(model, 'groq:fast')
            calls.clear()
            lvl_script._llm([])
            self.assertEqual(calls, ['fast'])   # the slow one is not tried again for a while

    def test_all_models_failing_raises_a_script_error_naming_each(self):
        def broken(provider, model, messages, **kw):raise lvl_script.text_llm.Unavailable('quota', 60)
        with tempfile.TemporaryDirectory() as tmp, patch.object(lvl_script, 'SLOW_FILE', Path(tmp) / 'slow.json'), \
                patch.object(lvl_script.text_llm, 'candidates', return_value=[('groq', 'a'), ('openrouter', 'b')]), patch.object(lvl_script.text_llm, 'call', side_effect=broken):
            with self.assertRaises(lvl_script.ScriptError) as ctx:lvl_script._llm([])
        self.assertIn('groq:a', str(ctx.exception))
        self.assertIn('openrouter:b', str(ctx.exception))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        import lvl_pipeline, lvl_worker
        self.w, self.p = lvl_worker, lvl_pipeline
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [patch.object(lvl_pipeline, 'RUNS', root / 'runs'), patch.object(lvl_worker, 'STATE', root / 'state.json'), patch.object(lvl_worker, 'CONFIG', root / 'brands.json'),
            patch.object(lvl_worker,'upgrade_pending')]
        for p in self.patches:p.start()
        self.run = root / 'runs' / 'lvl-1'
        (self.run).mkdir(parents=True)
        self.manifest = {'run_id': 'lvl-1', 'status': 'ready', 'videos': {'ru': 'ru.mp4', 'en': 'en.mp4'}}
        (self.run / 'manifest.json').write_text(json.dumps(self.manifest))
        (self.run / 'script.json').write_text(json.dumps({'episode': {'title_ru': 'T', 'title_en': 'T', 'evidence': [], 'description_ru': '', 'description_en': ''}}))
        self.cfg = {'lvl_enabled': True, 'post_hour_utc': {'ru': 15, 'en': 0}, 'publishing': {'telegram': {'enabled': True}, 'youtube': {'enabled': True}}}

    def tearDown(self):
        for p in self.patches:p.stop()
        self.tmp.cleanup()

    def test_last_slot_is_the_most_recent_occurrence(self):
        noon = 1_800_000_000 - 1_800_000_000 % 86400 + 12 * 3600
        self.assertEqual(self.w.last_slot(15, noon), noon - 12 * 3600 + 15 * 3600 - 86400)
        self.assertEqual(self.w.last_slot(10, noon), noon - 2 * 3600)

    def interval_config(self):
        return {**self.cfg,'languages':['ru','en'],'publish_interval_seconds':7200}

    def test_interval_posts_one_language_per_account_then_alternates_without_a_catchup_burst(self):
        cfg=self.interval_config();self._second_episode();state={};calls=[]
        def send(platform,lang,manifest,*args):
            calls.append((platform,lang,manifest['run_id']))
            return {'status':'published','url':'https://example.test/'+str(len(calls))}
        with patch.object(self.w,'now',return_value=100000) as clock,patch.object(self.w,'publish_one',side_effect=send),patch.object(self.w,'_announce'):
            self.assertEqual(self.w.publish_due(cfg,state,lambda *_:None),2)
            self.assertEqual(self.w.publish_due(cfg,{},lambda *_:None),0)  # reload persisted receipts, no process memory
            clock.return_value=107199
            self.assertEqual(self.w.publish_due(cfg,state,lambda *_:None),0)
            clock.return_value=107200
            self.assertEqual(self.w.publish_due(cfg,state,lambda *_:None),2)
            self.assertEqual(self.w.publish_due(cfg,state,lambda *_:None),0)
            clock.return_value=130000  # a long outage permits just one new video per account
            self.assertEqual(self.w.publish_due(cfg,state,lambda *_:None),2)
            self.assertEqual(self.w.publish_due(cfg,state,lambda *_:None),0)
        self.assertEqual([c[1] for c in calls],['ru','ru','en','en','ru','ru'])
        self.assertEqual(json.loads((self.run/'manifest.json').read_text())['status'],'published')

    def test_interval_retries_frozen_source_and_does_not_block_other_accounts(self):
        cfg=self.interval_config();state={};calls=[]
        def send(platform,lang,manifest,*args):
            calls.append((platform,lang,manifest['run_id']))
            if platform=='youtube' and len(calls)==2:raise RuntimeError('upload confirmation pending')
            return {'status':'published','url':'u'}
        with patch.object(self.w,'now',return_value=100000),patch.object(self.w,'publish_one',side_effect=send),patch.object(self.w,'_alert'),patch.object(self.w,'_announce'):
            self.assertEqual(self.w.publish_due(cfg,state,lambda *_:None),1)
            state=json.loads(self.w.STATE.read_text())
            self.assertEqual(self.w.publish_due(cfg,state,lambda *_:None),1)
        self.assertEqual(calls,[('telegram','ru','lvl-1'),('youtube','ru','lvl-1'),('youtube','ru','lvl-1')])
        self.assertEqual(state['interval_pending'],{})

    def test_interval_counts_receipts_from_held_episodes_and_ignores_old_daily_served_state(self):
        cfg=self.interval_config();held=self._second_episode()
        (held/'manifest.json').write_text(json.dumps({**self.manifest,'status':'held_for_visual_revision','published':{'youtube:en':{'at':99999,'url':'old'}}}))
        with patch.object(self.w,'now',return_value=100000),patch.object(self.w,'publish_one',return_value={'status':'published','url':'u'}) as send,patch.object(self.w,'_announce'):
            self.assertEqual(self.w.publish_due(cfg,{'served':{'ru':200000,'en':200000}},lambda *_:None),1)
        self.assertEqual(send.call_args.args[:2],('telegram','ru'))

    def test_interval_generation_refills_an_empty_destination_even_if_another_one_has_a_backlog(self):
        cfg={**self.interval_config(),'backlog_target':2}
        for p in (self.run,self._second_episode()):
            m=json.loads((p/'manifest.json').read_text())
            m['published']={f'telegram:{lang}':{'at':1,'url':'u'} for lang in ('ru','en')}
            (p/'manifest.json').write_text(json.dumps(m))
        self.assertTrue(self.w.need_production(cfg,{}))

    def test_a_busy_host_postpones_production_without_counting_a_failure(self):
        (self.run.parent.parent / 'brands.json').write_text(json.dumps({'lvl': {**self.cfg, 'backlog_target': 3}}))
        with patch.object(self.w, 'LOCK', self.run.parent.parent / '.lock'), patch.object(self.p, 'produce', side_effect=self.p.HostBusy('host busy: 900 MiB available')), \
                patch.object(self.w, 'publish_due', return_value=0):
            result = self.w.tick(lambda *_: None)
        state = json.loads((self.run.parent.parent / 'state.json').read_text())
        self.assertEqual(result['status'], 'ok')
        self.assertIn('produce_failed_at', state)
        self.assertNotIn('produce_failures', state)
        self.assertNotIn('last_produce_error', state)

    def tick_with(self, state, produce, backlog_target=3):
        folder = self.run.parent.parent
        (folder / 'brands.json').write_text(json.dumps({'lvl': {**self.cfg, 'backlog_target': backlog_target}}))
        (folder / 'state.json').write_text(json.dumps(state))
        with patch.object(self.w, 'LOCK', folder / '.lock'), patch.object(self.p, 'produce', side_effect=produce) as call, \
                patch.object(self.w, 'publish_due', return_value=0), patch.object(self.w, '_alert') as alert:
            self.w.tick(lambda *_: None)
        return call, alert, json.loads((folder / 'state.json').read_text())

    def test_deferral_starts_a_clock_and_a_host_that_stays_busy_for_hours_is_rendered_on_anyway(self):
        import time
        call, _, state = self.tick_with({}, self.p.HostBusy('host busy: 900 MiB available'))
        self.assertFalse(call.call_args.kwargs['force'])
        self.assertAlmostEqual(state['host_busy_since'], time.time(), delta=60)
        long_ago = {'host_busy_since': time.time() - self.w.HOST_BUSY_LIMIT - 60}
        call, _, state = self.tick_with(long_ago, [{'run_id': 'lvl-2'}])
        self.assertTrue(call.call_args.kwargs['force'])
        self.assertNotIn('host_busy_since', state)
        self.assertAlmostEqual(state['last_produced_at'], time.time(), delta=60)

    def test_the_owner_is_told_when_no_episode_appears_for_a_day_and_not_before(self):
        import time
        _, alert, state = self.tick_with({'watch_since': time.time() - 40 * 3600, 'last_produced_at': time.time() - 25 * 3600, 'last_produce_error': 'ScriptError: too short'}, self.p.HostBusy('x'))
        alert.assert_called_once()
        self.assertEqual(alert.call_args.kwargs['cooldown'], 12 * 3600)
        self.assertIn('no new episode for 25 h', str(alert.call_args.args[1]))
        self.assertIn('too short', str(alert.call_args.args[1]))
        _, alert, _ = self.tick_with({'watch_since': time.time() - 40 * 3600, 'last_produced_at': time.time() - 2 * 3600}, self.p.HostBusy('x'))
        alert.assert_not_called()
        _, alert, _ = self.tick_with({'watch_since': time.time() - 40 * 3600, 'last_produced_at': time.time() - 30 * 3600}, self.p.HostBusy('x'), backlog_target=1)
        alert.assert_not_called()   # a full backlog needs no production, so quiet is normal

    def test_a_series_switched_back_on_after_a_pause_is_not_reported_as_stalled(self):
        import time
        old = {'watch_since': time.time() - 90 * 3600, 'last_tick_at': time.time() - 80 * 3600, 'last_produced_at': time.time() - 85 * 3600}
        _, alert, state = self.tick_with(old, self.p.HostBusy('x'))
        alert.assert_not_called()
        self.assertAlmostEqual(state['watch_since'], time.time(), delta=60)

    def test_the_stall_clock_starts_when_the_series_is_first_seen_enabled(self):
        import time
        _, alert, state = self.tick_with({}, self.p.HostBusy('x'))
        alert.assert_not_called()
        self.assertAlmostEqual(state['watch_since'], time.time(), delta=60)

    def test_posts_carry_title_description_and_tags_but_no_source_links(self):
        episode = {'title_ru': 'Заголовок', 'description_ru': 'Описание', 'hashtags_ru': ['#a', '#b', '#c', '#d'],
                   'evidence': [{'title': 'Daily steps and mortality', 'source': 'PubMed', 'year': '2022', 'url': 'https://pubmed.ncbi.nlm.nih.gov/1/'}]}
        text = self.w.caption(episode, 'ru', self.cfg, True)
        self.assertEqual(text.split('\n\n')[:2], ['Заголовок', 'Описание'])
        self.assertTrue(text.endswith('#a #b #c'))
        for noise in ('http', 'PubMed', 'Источник', 'Source'):self.assertNotIn(noise, text)

    def test_a_slot_publishes_once_per_platform_and_language(self):
        slot = self.w.last_slot(15, 1_800_000_000)
        calls = []
        def fake(platform, lang, manifest, episode, cfg):
            calls.append((platform, lang));return {'status': 'published', 'url': f'https://x/{platform}/{lang}'}
        state = {}
        with patch.object(self.w, 'now', return_value=slot + 600), patch.object(self.w, 'publish_one', side_effect=fake):
            self.assertEqual(self.w.publish_due(self.cfg, state, lambda *_: None), 2)
            self.assertEqual(self.w.publish_due(self.cfg, state, lambda *_: None), 0)
        self.assertEqual(sorted(calls), [('telegram', 'ru'), ('youtube', 'ru')])
        saved = json.loads((self.run / 'manifest.json').read_text())
        self.assertEqual(set(saved['published']), {'telegram:ru', 'youtube:ru'})

    def test_a_failed_platform_is_retried_without_repeating_the_published_one(self):
        slot = self.w.last_slot(15, 1_800_000_000)
        outcomes = iter([{'status': 'published', 'url': 'u1'}, RuntimeError('login lost'), {'status': 'published', 'url': 'u2'}])
        calls = []
        def fake(platform, lang, *a):
            calls.append(platform);r = next(outcomes)
            if isinstance(r, Exception):raise r
            return r
        state = {}
        with patch.object(self.w, 'now', return_value=slot + 600), patch.object(self.w, 'publish_one', side_effect=fake), patch.object(self.w, '_alert') as alert:
            self.assertEqual(self.w.publish_due(self.cfg, state, lambda *_: None), 1)
            alert.assert_called_once()
            self.assertEqual(self.w.publish_due(self.cfg, state, lambda *_: None), 1)
        self.assertEqual(calls, ['telegram', 'youtube', 'youtube'])

    def _second_episode(self):
        run = self.run.parent / 'lvl-2'
        run.mkdir()
        (run / 'manifest.json').write_text(json.dumps({**self.manifest, 'run_id': 'lvl-2'}))
        (run / 'script.json').write_text((self.run / 'script.json').read_text())
        return run

    def test_a_platform_enabled_later_catches_up_without_holding_the_new_episode_back(self):
        slot = self.w.last_slot(15, 1_800_000_000)
        second = self._second_episode()
        old = dict(self.manifest, published={'telegram:ru': {'url': 'u', 'at': slot - 86400}})
        (self.run / 'manifest.json').write_text(json.dumps(old))
        calls = []
        def fake(platform, lang, manifest, *a):
            calls.append((platform, manifest['run_id']));return {'status': 'published', 'url': 'u'}
        cfg = {'lvl_enabled': True, 'post_hour_utc': {'ru': 15}, 'publishing': {'telegram': {'enabled': True}, 'tiktok': {'enabled': True}}}
        state = {}
        with patch.object(self.w, 'now', return_value=slot + 600), patch.object(self.w, 'publish_one', side_effect=fake):
            self.assertEqual(self.w.publish_due(cfg, state, lambda *_: None), 2)
            self.assertEqual(self.w.publish_due(cfg, state, lambda *_: None), 0)
        self.assertEqual(calls, [('telegram', 'lvl-2'), ('tiktok', 'lvl-1')])
        self.assertEqual(state['served']['ru'], slot)
        self.assertEqual(json.loads((self.run / 'manifest.json').read_text())['status'], 'published')
        self.assertEqual(json.loads((second / 'manifest.json').read_text())['status'], 'ready')

    def test_a_retry_in_the_slot_does_not_send_a_second_episode_to_the_platform_that_succeeded(self):
        slot = self.w.last_slot(15, 1_800_000_000)
        self._second_episode()
        outcomes = iter([{'status': 'published', 'url': 'u1'}, RuntimeError('boom'), {'status': 'published', 'url': 'u2'}])
        calls = []
        def fake(platform, lang, manifest, *a):
            calls.append((platform, manifest['run_id']));r = next(outcomes)
            if isinstance(r, Exception):raise r
            return r
        cfg = {**self.cfg, 'post_hour_utc': {'ru': 15}}
        state = {}
        with patch.object(self.w, 'now', return_value=slot + 600), patch.object(self.w, 'publish_one', side_effect=fake), patch.object(self.w, '_alert'):
            self.w.publish_due(cfg, state, lambda *_: None)
            self.assertNotIn('ru', state.get('served', {}))
            self.w.publish_due(cfg, state, lambda *_: None)
        self.assertEqual(calls, [('telegram', 'lvl-1'), ('youtube', 'lvl-1'), ('youtube', 'lvl-1')])
        self.assertEqual(state['served']['ru'], slot)

    def test_a_platform_asking_to_wait_is_asked_again_in_the_same_slot(self):
        slot = self.w.last_slot(15, 1_800_000_000)
        cfg = {'lvl_enabled': True, 'post_hour_utc': {'ru': 15}, 'publishing': {'tiktok': {'enabled': True}}}
        answers = iter([{'status': 'scheduled'}, {'status': 'published', 'url': 'u'}])
        state = {}
        with patch.object(self.w, 'now', return_value=slot + 600), patch.object(self.w, 'publish_one', side_effect=lambda *a: next(answers)):
            self.assertEqual(self.w.publish_due(cfg, state, lambda *_: None), 0)
            self.assertNotIn('ru', state.get('served', {}))
            self.assertEqual(self.w.publish_due(cfg, state, lambda *_: None), 1)

    def test_a_slot_missed_by_half_a_day_is_skipped(self):
        slot = self.w.last_slot(15, 1_800_000_000)
        state = {}
        with patch.object(self.w, 'now', return_value=slot + 13 * 3600), patch.object(self.w, 'publish_one') as pub:
            self.assertEqual(self.w.publish_due({**self.cfg, 'post_hour_utc': {'ru': 15}}, state, lambda *_: None), 0)
        pub.assert_not_called()

    def test_disabled_brand_does_nothing(self):
        (Path(self.tmp.name) / 'brands.json').write_text(json.dumps({'lvl': {'lvl_enabled': False}}))
        self.assertEqual(self.w.tick(lambda *_: None)['status'], 'disabled')

    def test_production_backs_off_after_a_failure(self):
        self.assertFalse(self.w.need_production({'backlog_target': 1}, {}))
        (self.run / 'manifest.json').write_text(json.dumps({**self.manifest, 'status': 'published'}))
        self.assertTrue(self.w.need_production({'backlog_target': 1}, {}))
        self.assertFalse(self.w.need_production({'backlog_target': 1}, {'produce_failed_at': self.w.now() - 60}))


class SpriteCacheTests(unittest.TestCase):
    def test_the_sprite_cache_is_bounded_by_bytes_and_keeps_recent_poses(self):
        import lvl_render
        lvl_render._sprite_cache.clear();lvl_render._sprite_bytes = 0
        spec = lvl_art.default_spec()
        budget = 3 * 120 * 120 * 4
        with patch.object(lvl_render, 'SPRITE_BUDGET', budget):
            for i in range(20):lvl_render.sprite(spec, {'arm_l': 12 * i}, 120)
            self.assertLessEqual(lvl_render._sprite_bytes, budget)
            self.assertLessEqual(len(lvl_render._sprite_cache), 3)
            again = lvl_render.sprite(spec, {'arm_l': 12 * 19}, 120)   # most recent pose is still cached
            self.assertIs(again, lvl_render.sprite(spec, {'arm_l': 12 * 19}, 120))
        lvl_render._sprite_cache.clear();lvl_render._sprite_bytes = 0

    def test_nearby_poses_share_one_sprite(self):
        import lvl_render
        spec = lvl_art.default_spec()
        a = lvl_render.sprite(spec, {'arm_l': 24, 'squash': 1.001, 'mouth': 0.52}, 100)
        b = lvl_render.sprite(spec, {'arm_l': 26, 'squash': 1.0, 'mouth': 0.5}, 100)
        self.assertIs(a, b)


@unittest.skipUnless(shutil.which('ffmpeg'), 'needs a local ffmpeg')
class PipelineTests(unittest.TestCase):
    """produce() wired end to end with the network and the text model replaced by fixtures; frames and mp4 files are real."""

    def setUp(self):
        import array, copy, math, subprocess
        import lvl_pipeline
        self.p = lvl_pipeline
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        rate = lvl_voice.RATE
        samples = array.array('h', [int(6000 * math.sin(i / 7)) for i in range(int(rate * 1.0))])
        self.voice = {'samples': samples, 'words': [{'text': 'раз', 'start': 0.0, 'end': 0.4}, {'text': 'два', 'start': 0.5, 'end': 0.9}], 'duration': 1.0, 'voice': 'fake'}
        self.episode = {'title_ru': 'Заголовок', 'title_en': 'Title', 'summary': 's', 'guest': None, 'claims': [{'id': 'c1', 'text_en': 'x', 'evidence': 'e1'}],
            'scenes': [{'speaker': 'lvl', 'text_ru': 'раз два', 'text_en': 'one two', 'setting': 'gym', 'claim': 'c1', 'actors': [{'id': 'lvl', 'side': 'center', 'expression': 'happy', 'action': 'cheer'}],
                'props': [{'icon': 'dumbbell', 'x': 0.5, 'y': 0.25, 'at': 0.3}], 'popup': '+10 XP', 'level_up': False}]}
        self.episode['scenes']=[copy.deepcopy(self.episode['scenes'][0]) for _ in range(3)]
        raw=[]
        for i,color in enumerate(('purple','orange')):
            path=root/f'fixture-insert-{i}.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c={color}:s=720x1280:r=24',
                '-t','1.5','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(path)],check=True)
            raw.append(path)
        def prepared_inserts(run,episode,prepared,log):
            # Network boundary only. The real decoder, compositor, encoder and
            # technical gates still operate on two distinct fixture MP4 files.
            return {'system':'lvl-hybrid-v1','scenes':{str(i):{'raw_video':str(path)} for i,path in zip((1,3),raw)}}
        def actual_review(video,episode,scenes,lang,target):
            import hashlib
            return {'video_sha256':hashlib.sha256(video.read_bytes()).hexdigest(),'publishable':True,'score_1_to_5':4,
                'scenes':[{'id':i+1,'matches_narration':True,'meaningful_action_or_edit':True} for i in range(len(scenes))]}
        topic = {'phrase': 'how to build muscle', 'title_en': 'Build muscle', 'title_ru': 'Мышцы', 'evidence_query_en': 'muscle', 'broad_query_en': '', 'angle': 'a', 'rubric': 'body'}
        evidence = [{'id': 'e1', 'source': 'PubMed', 'title': 't', 'year': '2024', 'journal': 'J', 'kind': 'review', 'url': 'u', 'text': 'x' * 300, 'relevance': 1.0},
            {'id': 'e2', 'source': 'PubMed', 'title': 't2', 'year': '2023', 'journal': 'J', 'kind': 'review', 'url': 'u2', 'text': 'y' * 300, 'relevance': 0.9}]
        self.patches = [patch.object(lvl_pipeline, 'RUNS', root / 'runs'), patch.object(lvl_pipeline, 'LOCK', root / 'lock'), patch.object(lvl_pipeline, 'MIN_SECONDS', 2.0),
            patch.object(lvl_cast, 'REGISTRY', root / 'characters.json'), patch.object(lvl_topics, 'DATA', root / 'lvl'),
            patch.object(lvl_pipeline.lvl_script, 'pick_topic', return_value=topic), patch.object(lvl_pipeline.lvl_topics, 'evidence', return_value=(evidence, {})),
            patch.object(lvl_pipeline.lvl_script, 'write_episode', return_value=self.episode), patch.object(lvl_pipeline.lvl_voice, 'speak', return_value=self.voice),
            patch.object(lvl_pipeline.lvl_hybrid,'ensure',side_effect=prepared_inserts),patch.object(lvl_pipeline.lvl_hybrid,'inspect',side_effect=actual_review),
            patch.object(lvl_pipeline, 'host_headroom', return_value=(8000, 0.0))]
        for q in self.patches:q.start()

    def tearDown(self):
        for q in self.patches:q.stop()
        self.tmp.cleanup()

    def test_an_episode_is_rendered_in_both_languages_and_recorded(self):
        manifest = self.p.produce(log=lambda *_: None)
        self.assertEqual(manifest['status'], 'ready')
        run = self.p.RUNS / manifest['run_id']
        for lang in ('ru', 'en'):
            self.assertTrue(Path(manifest['videos'][lang]).exists())
            self.assertFalse((Path(manifest['videos'][lang]).parent/'mix.wav').exists())
            self.assertGreater(manifest['gates'][lang]['mean_volume_db'], -38)
        self.assertEqual(set(manifest['sha256']), {'ru', 'en'})
        self.assertTrue((run / 'script.json').exists())
        self.assertEqual([t['run_id'] for t in lvl_topics.used()], [manifest['run_id']])
        self.assertEqual(lvl_cast.load()['lvl']['appearances'], 1)

    def test_a_render_failure_leaves_a_failed_manifest_and_no_topic_record(self):
        with patch.object(self.p.lvl_render, 'render', side_effect=RuntimeError('ffmpeg failed')):
            with self.assertRaises(RuntimeError):self.p.produce(log=lambda *_: None)
        manifests = [json.loads(m.read_text()) for m in self.p.RUNS.glob('*/manifest.json')]
        self.assertEqual([m['status'] for m in manifests], ['failed'])
        self.assertIn('ffmpeg failed', manifests[0]['error'])
        self.assertEqual(lvl_topics.used(), [])

    def test_a_failed_render_is_finished_later_without_writing_a_new_script(self):
        with patch.object(self.p.lvl_voice, 'speak', side_effect=self.p.lvl_voice.VoiceError('speech service unreachable')):
            with self.assertRaises(self.p.lvl_voice.VoiceError):self.p.produce(log=lambda *_: None)
        failed = [json.loads(m.read_text()) for m in self.p.RUNS.glob('*/manifest.json')]
        self.assertEqual([(m['status'], m['resumable'], m['attempts']) for m in failed], [('failed', True, 1)])
        with patch.object(self.p.lvl_script, 'pick_topic', side_effect=AssertionError('must not choose a new topic')), \
                patch.object(self.p.lvl_script, 'write_episode', side_effect=AssertionError('must not rewrite the script')):
            manifest = self.p.produce(log=lambda *_: None)
        self.assertEqual((manifest['status'], manifest['run_id'], manifest['attempts'], manifest['episode']), ('ready', failed[0]['run_id'], 2, 1))
        self.assertNotIn('resumable', manifest)
        self.assertNotIn('error', manifest)
        self.assertEqual(len(list(self.p.RUNS.glob('*/manifest.json'))), 1)
        self.assertEqual([t['run_id'] for t in lvl_topics.used()], [manifest['run_id']])

    def test_after_three_attempts_a_run_is_abandoned_and_a_new_episode_is_written(self):
        with patch.object(self.p.lvl_voice, 'speak', side_effect=self.p.lvl_voice.VoiceError('down')):
            for _ in range(3):
                with self.assertRaises(self.p.lvl_voice.VoiceError):self.p.produce(log=lambda *_: None)
        runs = list(self.p.RUNS.glob('*/manifest.json'))
        self.assertEqual(json.loads(runs[0].read_text())['attempts'], 3)
        manifest = self.p.produce(log=lambda *_: None)
        self.assertEqual(manifest['status'], 'ready')
        self.assertNotEqual(manifest['run_id'], json.loads(runs[0].read_text())['run_id'])
        self.assertEqual(manifest['episode'], 1)  # failed runs do not count as episodes

    def test_a_server_short_of_memory_defers_production_without_touching_anything(self):
        for headroom in ((900, 0.0), (8000, 60.0)):
            with patch.object(self.p, 'host_headroom', return_value=headroom):
                with self.assertRaises(self.p.HostBusy):self.p.produce(log=lambda *_: None)
        self.assertFalse(self.p.RUNS.exists() and list(self.p.RUNS.glob('*')))
        self.assertIsNone(self.p.check_host(lambda: (None, None)))   # unknown readings never block production

    def test_force_skips_the_host_check_only(self):
        with patch.object(self.p, 'host_headroom', return_value=(900, 0.0)), patch.object(self.p, 'write_new_episode', side_effect=RuntimeError('reached the script step')):
            with self.assertRaises(self.p.HostBusy):self.p.produce(log=lambda *_: None)
            with self.assertRaisesRegex(RuntimeError, 'reached the script step'):self.p.produce(log=lambda *_: None, force=True)

    def test_rerender_replaces_only_the_requested_language_and_keeps_the_receipts(self):
        import hashlib
        manifest = self.p.produce(log=lambda *_: None)
        run = self.p.RUNS / manifest['run_id']
        ru_before = hashlib.sha256(Path(manifest['videos']['ru']).read_bytes()).hexdigest()
        manifest['published'] = {'telegram:ru': {'url': 'https://t.me/x/3'}}
        self.p.save(run / 'manifest.json', manifest)
        script = json.loads((run / 'script.json').read_text())
        script['episode']['scenes'][0]['text_en'] = 'three four'
        self.p.save(run / 'script.json', script)
        with patch.object(self.p.lvl_voice, 'speak', return_value=self.voice) as speak:
            again = self.p.rerender(manifest['run_id'], ['en'], log=lambda *_: None)
        spoken = ' '.join(str(c) for c in speak.call_args_list)
        self.assertIn('three four', spoken)
        self.assertNotIn('раз два', spoken)
        self.assertEqual(hashlib.sha256(Path(again['videos']['ru']).read_bytes()).hexdigest(), ru_before)
        self.assertEqual((again['status'], again['published'], list(again['rerendered'])), ('ready', {'telegram:ru': {'url': 'https://t.me/x/3'}}, ['en']))
        self.assertEqual(again['sha256']['en'], hashlib.sha256(Path(again['videos']['en']).read_bytes()).hexdigest())
        manifest['status'] = 'failed'
        self.p.save(run / 'manifest.json', manifest)
        with self.assertRaisesRegex(self.p.ProduceError, 'only ready episodes'):self.p.rerender(manifest['run_id'], ['en'])

    def test_a_run_killed_mid_render_is_resumed_like_a_failed_one(self):
        with patch.object(self.p.lvl_voice, 'speak', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):self.p.produce(log=lambda *_: None)
        path = next(self.p.RUNS.glob('*/manifest.json'))
        killed = json.loads(path.read_text());killed.update(status='rendering', resumable=False)  # what a SIGTERM leaves behind: the manifest never reached 'failed'
        path.write_text(json.dumps(killed))
        manifest = self.p.produce(log=lambda *_: None)
        self.assertEqual((manifest['status'], manifest['run_id'], manifest['attempts']), ('ready', killed['run_id'], 2))

    def test_an_old_failed_run_is_not_resumed(self):
        with patch.object(self.p.lvl_voice, 'speak', side_effect=self.p.lvl_voice.VoiceError('down')):
            with self.assertRaises(self.p.lvl_voice.VoiceError):self.p.produce(log=lambda *_: None)
        self.assertIsNotNone(self.p.find_resumable())
        self.assertIsNone(self.p.find_resumable(now=time.time() + self.p.RESUME_WINDOW + 60))

    def test_a_script_the_checker_rejects_moves_on_to_another_topic(self):
        second = {'phrase': 'sleep and mood', 'title_en': 'Sleep', 'title_ru': 'Сон', 'evidence_query_en': 'sleep', 'broad_query_en': '', 'angle': 'a', 'rubric': 'energy'}
        first = self.p.lvl_script.pick_topic.return_value
        asked = []
        def pick(rubric, exclude=()):
            asked.append(list(exclude))
            return second if exclude else first
        with patch.object(self.p.lvl_script, 'pick_topic', side_effect=pick), \
                patch.object(self.p.lvl_script, 'write_episode', side_effect=[self.p.lvl_script.ScriptError('fact review: claim not in source'), self.episode]) as write:
            manifest = self.p.produce(log=lambda *_: None)
        self.assertEqual(manifest['status'], 'ready')
        self.assertEqual(manifest['topic']['phrase'], 'sleep and mood')
        self.assertEqual(asked[1], ['how to build muscle'])
        self.assertEqual(write.call_count, 2)

    def test_when_every_topic_is_rejected_the_failure_names_the_reasons(self):
        with patch.object(self.p.lvl_script, 'write_episode', side_effect=self.p.lvl_script.ScriptError('fact review: not in source')):
            with self.assertRaises(self.p.ProduceError) as ctx:self.p.produce(log=lambda *_: None)
        self.assertIn('rejected: how to build muscle: fact review: not in source', str(ctx.exception))
        self.assertEqual(list(self.p.RUNS.glob('*/manifest.json')), [])

    def test_too_little_evidence_means_another_topic_then_a_clear_failure(self):
        weak = [{'id': 'e1', 'source': 'PubMed', 'title': 't', 'year': '', 'journal': '', 'kind': 'review', 'url': 'u', 'text': 'x' * 300, 'relevance': 0.7}]
        with patch.object(self.p.lvl_topics, 'evidence', return_value=(weak, {})):
            with self.assertRaises(self.p.ProduceError) as ctx:self.p.produce(log=lambda *_: None)
        self.assertIn('no topic produced an acceptable episode', str(ctx.exception))
        self.assertIn('too little relevant evidence', str(ctx.exception))
        self.assertEqual(list(self.p.RUNS.glob('*/manifest.json')), [])


class EncoderCommandTests(unittest.TestCase):
    def test_the_encoder_is_run_lean_and_never_with_shortest(self):
        """'-shortest' made ffmpeg buffer both inputs and grow to ~960 MB on the 4.9 GB server (about 220 MB with '-t'); the thrash took the live pipeline down with it."""
        import array, math
        import lvl_render
        samples = array.array('h', [int(5000 * math.sin(i / 9)) for i in range(int(lvl_voice.RATE * 0.6))])
        scene = {'setting': 'gym', 'seed': 0, 'samples': samples, 'words': [{'text': 'раз', 'start': 0.0, 'end': 0.5}], 'duration': 0.6,
            'envelope': lvl_voice.envelope(samples), 'actors': [{'spec': lvl_art.default_spec(), 'side': 'center', 'expression': 'happy', 'action': 'none', 'speaking': True}],
            'props': [], 'popup': None, 'popup_at': 0.5, 'xp_gain': 0, 'level_up': False}
        seen = {}

        class FakePipe:
            def write(self, data):seen['bytes'] = seen.get('bytes', 0) + len(data)
            def close(self):pass

        class FakeProc:
            stdin = FakePipe()
            class stderr:
                @staticmethod
                def read():return b''
                @staticmethod
                def close():pass
            def wait(self, timeout=None):return 0
            def kill(self):pass

        def fake_popen(cmd, **kwargs):
            seen['cmd'] = cmd
            return FakeProc()

        with tempfile.TemporaryDirectory() as tmp, patch.object(lvl_render.subprocess, 'Popen', fake_popen), \
                patch.object(lvl_render.lvl_voice, 'ffmpeg_cmd', lambda name, *args, **kw: [name, *args]):
            result = lvl_render.render([scene], Path(tmp) / 'a.mp4', Path(tmp) / 'w', 'Тест', None, ('конец',), lvl_art.default_spec())
        cmd = seen['cmd']
        self.assertNotIn('-shortest', cmd)
        self.assertEqual(cmd[cmd.index('-t') + 1], f"{result['duration']:.3f}")
        self.assertEqual(cmd[cmd.index('-threads') + 1], '2')
        self.assertIn('rc-lookahead=8', cmd[cmd.index('-x264-params') + 1])
        self.assertGreater(seen['bytes'], 10_000)


@unittest.skipUnless(shutil.which('ffmpeg'), 'needs a local ffmpeg')
class RenderTests(unittest.TestCase):
    def test_one_scene_renders_to_a_vertical_video_with_audio(self):
        import array, math
        import lvl_pipeline, lvl_render
        rate = lvl_voice.RATE
        samples = array.array('h', [int(5000 * math.sin(i / 9)) for i in range(int(rate * 1.2))])
        scene = {'setting': 'gym', 'seed': 0, 'samples': samples, 'words': [{'text': 'раз', 'start': 0.0, 'end': 0.5}, {'text': 'два', 'start': 0.6, 'end': 1.1}], 'duration': 1.2,
            'envelope': lvl_voice.envelope(samples), 'actors': [{'spec': lvl_art.default_spec(), 'side': 'center', 'expression': 'happy', 'action': 'cheer', 'speaking': True}],
            'props': [{'icon': 'dumbbell', 'x': 0.5, 'y': 0.25, 'at': 0.3}], 'popup': '+10 XP', 'popup_at': 0.5, 'xp_gain': 0.3, 'level_up': False}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'a.mp4'
            result = lvl_render.render([scene], out, Path(tmp) / 'w', 'Тест', None, ('конец',), lvl_art.default_spec())
            info = lvl_pipeline.probe(out)
            kinds = {s['codec_type']: s for s in info['streams']}
            self.assertEqual((kinds['video']['width'], kinds['video']['height']), (1080, 1920))
            self.assertIn('audio', kinds)
            self.assertAlmostEqual(float(info['format']['duration']), result['duration'], delta=0.6)


if __name__ == '__main__':unittest.main()
