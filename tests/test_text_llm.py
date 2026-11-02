"""Provider order, cooldowns and the approved-model guards of the text-model chain."""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import text_llm as t

MESSAGES = [{'role': 'system', 'content': 'Return JSON'}, {'role': 'user', 'content': 'source'}]


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(t, 'COOLDOWNS', Path(self.tmp.name) / 'cooldowns.json'),
                        patch.object(t, 'ORDER', ('agy', 'openrouter', 'groq'))]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def test_a_second_account_answers_after_the_first_runs_out_and_keeps_its_own_cooldown(self):
        base=Path(self.tmp.name);(base/'data').mkdir()
        (base/'data'/'agy_accounts.json').write_text(json.dumps(['/srv/second']))
        answer=type('R',(),{'returncode':0,'stdout':json.dumps({'status':'SUCCESS','response':'{"x": 2}'}),'stderr':''})
        spent=type('R',(),{'returncode':1,'stdout':json.dumps({'status':'ERROR','error':'Individual quota reached. Resets in 2h0m0s.'}),'stderr':''})
        with patch.object(t,'BASE_DIR',base):
            pairs=t.candidates([])
            self.assertEqual(pairs[:3],[('agy','gemini-3.8-flash-high'),('agy2','gemini-3.8-flash-high'),('agy','claude-sonnet-4-6')])
            with patch.object(t.os,'access',return_value=True),patch.object(t.subprocess,'run',side_effect=[spent,answer]) as run:
                result,label=t.complete(MESSAGES,groq_models=[])
            self.assertEqual((result,label),({'x':2},'agy2:gemini-3.8-flash-high'))
            self.assertEqual(run.call_args.kwargs['env']['HOME'],'/srv/second')
            self.assertNotEqual(run.call_args_list[0].kwargs['env']['HOME'],'/srv/second')
            pairs=t.candidates([])
            self.assertNotIn(('agy','gemini-3.8-flash-high'),pairs)
            self.assertIn(('agy2','gemini-3.8-flash-high'),pairs)

    def test_only_the_model_that_accepts_it_gets_the_effort_flag_and_quotas_are_per_model(self):
        answer=type('R',(),{'returncode':0,'stdout':json.dumps({'status':'SUCCESS','response':'{"x": 2}'}),'stderr':''})
        with patch.object(t.os,'access',return_value=True),patch.object(t.subprocess,'run',return_value=answer) as run:
            t.call('agy','claude-sonnet-4-6',MESSAGES)
            self.assertNotIn('--effort',run.call_args.args[0])
            t.call('agy','gemini-3.8-flash-high',MESSAGES)
            self.assertIn('--effort',run.call_args.args[0])
        self.assertIn(('agy','claude-sonnet-4-6'),t.candidates([]))
        t.COOLDOWNS.write_text(json.dumps({'agy:gemini-3.8-flash-high': time.time()+3600}))
        pairs=t.candidates([])
        self.assertNotIn(('agy','gemini-3.8-flash-high'),pairs)
        self.assertEqual(pairs[0],('agy','claude-sonnet-4-6'))

    def test_owner_policy_order_is_subscription_then_free_openrouter_then_groq_last(self):
        providers = [p for p, _ in t.candidates(['qwen/qwen3.8-27b'])]
        self.assertEqual(providers[0], 'agy')
        self.assertEqual(providers[-1], 'groq')
        self.assertEqual(providers.index('openrouter') > providers.index('agy'), True)

    def test_exhausted_subscription_is_skipped_and_not_probed_again_until_cooldown_ends(self):
        quota = t.Unavailable('Antigravity CLI: RESOURCE_EXHAUSTED quota', 1800)
        with patch.object(t, '_agy', side_effect=quota) as agy, patch.object(t, '_openrouter', return_value='{"ok": 1}'):
            first, label = t.complete(MESSAGES, groq_models=['qwen/qwen3.8-27b'])
            second, _ = t.complete(MESSAGES, groq_models=['qwen/qwen3.8-27b'])
        self.assertEqual(first, {'ok': 1})
        self.assertTrue(label.startswith('openrouter:'))
        self.assertEqual(agy.call_count, len(t.AGY_MODELS))  # each model has its own quota and is asked once
        self.assertNotIn('agy', [p for p, _ in t.candidates([])])

    def test_cooldown_expires(self):
        t.COOLDOWNS.write_text(json.dumps({'agy:gemini-3.8-flash-high': time.time() - 1}))
        self.assertIn('agy', [p for p, _ in t.candidates([])])

    def test_daily_groq_limit_is_recorded_for_its_stated_delay_and_not_waited_out(self):
        message = ('Rate limit reached for model `openai/gpt-oss-120b` on tokens per day (TPD): '
                   'Limit 200000, Used 200000. Please try again in 12m57.168s.')
        limited = Mock(status_code=429, headers={})
        limited.json.return_value = {'error': {'message': message}}
        with patch.dict(t.os.environ, GROQ_API_KEY='x'), patch.object(t.requests, 'post', return_value=limited), \
                patch.object(t.time, 'sleep') as sleep:
            with self.assertRaises(t.Unavailable) as caught:
                t.call('groq', 'openai/gpt-oss-120b', MESSAGES)
        sleep.assert_not_called()
        self.assertEqual(str(caught.exception), 'HTTP 429')
        remaining = json.loads(t.COOLDOWNS.read_text())['groq:openai/gpt-oss-120b'] - time.time()
        self.assertTrue(777 < remaining < 820, remaining)

    def test_short_per_minute_window_is_waited_out_and_the_same_request_retried(self):
        limited = Mock(status_code=429, headers={'retry-after': '2'})
        limited.json.return_value = {'error': {'message': 'tokens per minute'}}
        good = Mock(status_code=200)
        good.json.return_value = {'choices': [{'message': {'content': '{"a": 1}'}}]}
        with patch.dict(t.os.environ, GROQ_API_KEY='x'), patch.object(t.requests, 'post', side_effect=[limited, good]) as post, \
                patch.object(t.time, 'sleep') as sleep:
            self.assertEqual(t.call('groq', 'm', MESSAGES), '{"a": 1}')
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(3.0)

    def test_every_model_cooling_down_reports_why_instead_of_a_blank_failure(self):
        t.COOLDOWNS.write_text(json.dumps({f'{p}:{m}': time.time() + 600 for p, m in t.candidates(['g'])}))
        with self.assertRaisesRegex(t.Unavailable, 'cooling down'):
            t.complete(MESSAGES, groq_models=['g'])

    def test_invalid_json_is_not_a_pass_and_cools_a_non_groq_model_briefly(self):
        with patch.object(t, '_agy', return_value='no json here'):
            with self.assertRaises(t.Unavailable):
                t.call('agy', 'gemini-3.8-flash-high', MESSAGES)
        self.assertIn('agy:gemini-3.8-flash-high', json.loads(t.COOLDOWNS.read_text()))

    def test_json_text_tolerates_fences_and_prose_but_requires_an_object(self):
        self.assertEqual(json.loads(t.json_text('Sure:\n```json\n{"x": 1}\n```\nDone')), {'x': 1})
        with self.assertRaises(ValueError):t.json_text('[1, 2]')
        with self.assertRaises(ValueError):t.json_text('nothing')

    def test_retry_delay_formats(self):
        self.assertAlmostEqual(t.retry_delay('Please try again in 12m57.168s.'), 777.168)
        self.assertEqual(t.retry_delay('try again in 1h2m3s'), 3723)
        self.assertEqual(t.retry_delay('try again in 45.2s'), 45.2)
        self.assertEqual(t.retry_delay('x', {'retry-after': '30'}), 30)
        self.assertIsNone(t.retry_delay('no hint'))


class ProviderGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cool = patch.object(t, 'COOLDOWNS', Path(self.tmp.name) / 'cooldowns.json')
        self.cool.start()

    def tearDown(self):
        self.cool.stop()
        self.tmp.cleanup()

    def test_openrouter_never_calls_a_model_outside_the_approved_free_set(self):
        with patch.dict(t.os.environ, OPENROUTER_API_KEY='x'), patch.object(t.requests, 'post') as post:
            with self.assertRaises(ValueError):t.call('openrouter', 'openai/gpt-5', MESSAGES)
        post.assert_not_called()

    def test_openrouter_refuses_a_reply_that_reports_a_cost(self):
        paid = Mock(status_code=200)
        paid.json.return_value = {'choices': [{'message': {'content': '{}'}}], 'usage': {'cost': 0.01}}
        with patch.dict(t.os.environ, OPENROUTER_API_KEY='x'), patch.object(t.requests, 'post', return_value=paid):
            with self.assertRaisesRegex(t.Unavailable, 'nonzero cost'):
                t.call('openrouter', t.OPENROUTER_MODELS[0], MESSAGES)

    def test_cli_envelope_success_returns_the_json_object_inside_a_fenced_reply(self):
        done = Mock(returncode=0, stdout=json.dumps({'status': 'SUCCESS', 'response': '```json\n{"x": 2}\n```'}))
        with patch.object(t, 'AGY_BIN', sys.executable), patch.object(t.subprocess, 'run', return_value=done) as run:
            self.assertEqual(json.loads(t.call('agy', 'gemini-3.8-flash-high', MESSAGES)), {'x': 2})
        self.assertEqual(run.call_args.kwargs['env']['https_proxy'], t.AGY_PROXY)
        self.assertIn('gemini-3.8-flash-high', run.call_args.args[0])

    def test_cli_quota_error_gets_a_long_cooldown_and_timeout_a_short_one(self):
        quota = Mock(returncode=1, stdout=json.dumps({'status': 'ERROR', 'error': 'RESOURCE_EXHAUSTED quota exceeded'}))
        with patch.object(t, 'AGY_BIN', sys.executable), patch.object(t.subprocess, 'run', return_value=quota):
            with self.assertRaises(t.Unavailable) as caught:t.call('agy', 'gemini-3.8-flash-high', MESSAGES)
        self.assertEqual(caught.exception.retry_after, 1800)
        with patch.object(t, 'AGY_BIN', sys.executable), patch.object(t.subprocess, 'run', side_effect=subprocess.TimeoutExpired('agy', 150)):
            with self.assertRaises(t.Unavailable) as caught:t.call('agy', 'gemini-3.8-flash-high', MESSAGES)
        self.assertEqual(caught.exception.retry_after, 300)

    def test_a_content_safety_block_skips_the_prompt_but_does_not_pause_the_subscription(self):
        blocked = Mock(returncode=1, stdout=json.dumps({'status': 'ERROR', 'error': 'Your previous response was blocked by content safety filters: sensitive words'}))
        with patch.object(t, 'AGY_BIN', sys.executable), patch.object(t.subprocess, 'run', return_value=blocked):
            with self.assertRaises(t.Unavailable) as caught:t.call('agy', 'gemini-3.8-flash-high', MESSAGES)
        self.assertEqual(caught.exception.retry_after, 0)
        self.assertIn('agy', [p for p, _ in t.candidates([])])

    def test_oversized_prompt_is_not_passed_on_the_command_line(self):
        huge = [{'role': 'user', 'content': 'я' * 70000}]
        with patch.object(t, 'AGY_BIN', sys.executable), patch.object(t.subprocess, 'run') as run:
            with self.assertRaisesRegex(t.Unavailable, 'too large'):t.call('agy', 'gemini-3.8-flash-high', huge)
        run.assert_not_called()


if __name__ == '__main__':unittest.main()
