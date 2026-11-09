"""Fallback clients must launch OpenCode even without profile launch kwargs."""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class OpenCodeLaunchTests(unittest.TestCase):
    def load_bridge(self):
        acp = types.ModuleType('agent.copilot_acp_client')
        class Parent:
            def __init__(self, **kwargs):
                self.launch = kwargs
        acp.CopilotACPClient = Parent
        wire = types.ModuleType('agent.acp_openai_bridge')
        wire.render_tool_bridge_sections = lambda *a: []
        wire.extract_tool_calls_from_text = lambda *a: ([], '')
        spec = importlib.util.spec_from_file_location('opencode_launch_test',
            Path(__file__).resolve().parents[1]/'plugins/opencode-cli/bridge.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'agent.copilot_acp_client': acp, 'agent.acp_openai_bridge': wire}):
            spec.loader.exec_module(module)
        return module

    def test_fallback_and_auxiliary_callers_never_inherit_copilot_defaults(self):
        module = self.load_bridge()
        for kwargs in ({}, {'command': None, 'args': None}):
            client = module.OpenCodeClient(**kwargs)
            self.assertTrue(client.launch['command'].endswith('/.local/bin/hermes-opencode'))
            self.assertEqual(client.launch['args'], ['acp'])

    def test_explicit_launch_is_preserved(self):
        module = self.load_bridge()
        client = module.OpenCodeClient(command='/explicit/opencode', args=['acp', '--test'])
        self.assertEqual(client.launch['command'], '/explicit/opencode')
        self.assertEqual(client.launch['args'], ['acp', '--test'])
