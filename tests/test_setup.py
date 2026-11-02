import base64
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from sync_obsidian import selected_files, FILES, FOLDERS
from vpn_manager import parse_subscription, configuration, check_group
from model_policy import apply_model_policy, API_MODELS, APPROVED_MODELS, FALLBACKS


class ScopeTests(unittest.TestCase):
    def test_only_selected_notes_are_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in FILES:
                (root/name).write_text('selected')
            for name in FOLDERS:
                (root/name).mkdir()
                (root/name/'note.md').write_text('selected')
            (root/'private.md').write_text('excluded')
            (root/'Health'/'unselected.pdf').write_text('excluded')
            (root/'Health'/'leak.md').symlink_to(root/'private.md')
            result = {str(p.relative_to(root)) for p in selected_files(root)}
            expected = set(FILES) | {name+'/note.md' for name in FOLDERS}
            self.assertEqual(result, expected)

    def test_missing_selected_file_does_not_publish_partial_context(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                selected_files(Path(directory))


class SubscriptionTests(unittest.TestCase):
    def test_retired_vpn_provider_cannot_reappear(self):
        with self.assertRaises(ValueError):
            configuration({'EOS': {}, 'Taynik': {}}, 'secret')

    def test_grpc_reality_subscription_is_converted(self):
        raw = ('vless://11111111-1111-1111-1111-111111111111@example.com:443'
               '?type=grpc&serviceName=api&security=reality&sni=example.com'
               '&fp=safari&pbk=test-public-key&sid=1234#Test')
        data = base64.b64encode(raw.encode())
        node, = parse_subscription(data, 'Taynik')
        self.assertEqual(node['network'], 'grpc')
        self.assertEqual(node['grpc-opts'], {'grpc-service-name': 'api'})
        self.assertTrue(node['tls'])
        self.assertEqual(node['reality-opts']['public-key'], 'test-public-key')

    def test_subscription_cannot_replace_routes_or_bind_public_proxy(self):
        data = b'allow-lan: true\nrules: [MATCH,DIRECT]\nproxies:\n - {name: Test, type: vless, server: example.com, port: 443, uuid: dummy}\n'
        self.assertEqual(len(parse_subscription(data, 'EOS')), 1)
        config = configuration({'EOS': {'type': 'file', 'path': '/test.yaml'}}, 'secret')
        self.assertFalse(config['allow-lan'])
        self.assertEqual(config['bind-address'], '127.0.0.1')
        self.assertEqual(config['rules'][-1], 'MATCH,AI')
        listener = next(x for x in config['listeners'] if x['name']=='health-probe')
        self.assertEqual((listener['listen'], listener['port'], listener['proxy']),
                         ('127.0.0.1', 17891, 'PROBE'))
        cli = next(x for x in config['listeners'] if x['name']=='antigravity')
        self.assertEqual((cli['listen'], cli['port'], cli['proxy']),
                         ('127.0.0.1', 17892, 'ANTIGRAVITY'))

    def test_candidate_quota_does_not_change_or_misreport_active_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'.hermes').mkdir()
            (root/'.hermes/config.yaml').write_text('model: {default: gemini-3.8-flash}')
            def controller(endpoint, data=None, method=None):
                if endpoint == '/proxies/GEMINI' and method is None:
                    return {'now': 'original', 'all': ['original', 'candidate']}
                if endpoint == '/providers/proxies':
                    return {'providers': {}}
                if endpoint == '/proxies/PROBE':
                    return {}
                self.fail('Unexpected active route mutation: '+endpoint)
            with patch('vpn_manager.ROOT', root), \
                    patch('vpn_manager.Path.home', return_value=root), \
                    patch('vpn_manager.api', side_effect=controller), \
                    patch('vpn_manager.probe_gemini', return_value=('quota', 'rate_limit_or_quota')):
                calls = iter([('bad', 'timeout'), ('good', 'gemini_api_models')])
                result = check_group('GEMINI', lambda **kwargs: next(calls))
            self.assertEqual(result['selection'], 'original')
            self.assertEqual(result['status'], 'quota')
            self.assertFalse(result['switched'])


class ModelPolicyTests(unittest.TestCase):
    def test_existing_lite_fallback_and_auxiliary_are_replaced(self):
        config = {'fallback_providers': [{'provider':'gemini','model':'gemini-3.1-flash-lite'}],
                  'auxiliary': {'vision': {'provider':'gemini','model':'gemini-3.1-flash-lite'}}}
        apply_model_policy(config)
        self.assertEqual(config['agent']['reasoning_effort'], 'ultra')
        models = [config['model']['default']] + [m['model'] for m in config['fallback_providers']]
        self.assertEqual(models, [m for _,m in FALLBACKS])
        for entry in config['fallback_providers']:
            self.assertIn(entry['model'],APPROVED_MODELS[entry['provider']])
        for task in config['auxiliary'].values():
            self.assertNotIn('lite', task['model'])
            self.assertEqual(task['reasoning_effort'], 'ultra')

    def test_explicit_primary_cannot_be_lite(self):
        with self.assertRaises(ValueError):
            apply_model_policy({}, provider='gemini', primary='gemini-3.1-flash-lite')

    def test_paid_aggregator_models_cannot_be_selected(self):
        for provider,model in (('openrouter-free','nvidia/nemotron-3-ultra-550b-a55b'),
                               ('opencode-cli','opencode/deepseek-v4.1-flash')):
            with self.assertRaises(ValueError):
                apply_model_policy({},provider=provider,primary=model)

    def test_removed_providers_are_not_restored(self):
        config = {'providers': {'nvidia': {'api_key':'obsolete'},
                                'openrouter': {'api_key':'obsolete'},
                                'modelscope': {'enabled':True}},
                  'fallback_providers': [{'provider':'nvidia','model':'old'}]}
        apply_model_policy(config)
        self.assertFalse(config.get('providers'))
        self.assertNotIn('nvidia',{m['provider'] for m in config['fallback_providers']})
        self.assertTrue(all(m['model'].endswith(':free') for m in config['fallback_providers']
                            if m['provider']=='openrouter-free'))

    def test_removed_provider_cannot_be_restored_as_primary(self):
        for provider in ('nvidia','openrouter','modelscope'):
            with self.assertRaises(ValueError):
                apply_model_policy({},provider=provider,primary='any-model')

    def test_cli_source_respects_quality_order_and_keeps_native_vision(self):
        config = apply_model_policy({},provider='antigravity-cli')
        self.assertEqual(config['model']['default'],'gemini-3.8-flash-high')
        self.assertEqual([(x['provider'],x['model']) for x in config['fallback_providers']], [
            ('openrouter-free','nvidia/nemotron-3-ultra-550b-a55b:free'),
            ('openrouter-free','nvidia/nemotron-3.5-super-vl:free'),
            ('opencode-cli','opencode/deepseek-v4.1-flash-free'),
            ('opencode-cli','opencode/glm-5.3-flash-free'),
            ('groq','qwen/qwen3.8-27b')])
        self.assertEqual(config['auxiliary']['vision']['provider'],'gemini')
        self.assertEqual(config['auxiliary']['compression']['provider'],'antigravity-cli')
        self.assertEqual(config['agent']['reasoning_effort'],'ultra')
        with self.assertRaises(ValueError):
            apply_model_policy({},provider='antigravity-cli',primary='gemini-3.1-flash-lite')

    def test_gpt_oss_is_removed_and_cannot_be_selected(self):
        config = apply_model_policy({'model':{'provider':'groq','default':'openai/gpt-oss-120b'},
            'agent':{'reasoning_overrides':{'openai/gpt-oss-120b':'ultra'}},
            'fallback_providers':[{'provider':'groq','model':'openai/gpt-oss-120b'}]})
        self.assertNotIn('openai/gpt-oss-120b',config['agent']['reasoning_overrides'])
        self.assertNotIn('openai/gpt-oss-120b',[m for models in APPROVED_MODELS.values() for m in models])
        with self.assertRaises(ValueError):
            apply_model_policy({},provider='groq',primary='openai/gpt-oss-120b')


if __name__ == '__main__':
    unittest.main()
