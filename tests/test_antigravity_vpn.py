import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import antigravity_vpn as av


class CLIRouteTests(unittest.TestCase):
    def frames(self,model=av.MODEL,**result):
        return '\n'.join(json.dumps(f) for f in [
            {'event':'init','init':{'agent':'hermes-bridge','model':model}},
            {'event':'result','result':{'status':'SUCCESS','response':'READY',**result}}])

    def test_only_real_pinned_terminal_generation_confirms_a_route(self):
        self.assertEqual(av.classify_result(self.frames(),0)[0],'good')
        for output in (self.frames(model='flash-lite'),self.frames(status='WAITING'),
                       self.frames().splitlines()[0]):
            self.assertNotEqual(av.classify_result(output,0)[0],'good')

    def test_quota_on_candidate_never_promotes_it(self):
        mutations=[]
        def controller(path,data=None,method='GET'):
            if method=='PUT':mutations.append((path,data));return
            if path=='/proxies/ANTIGRAVITY':return {'now':'old'}
            if path=='/proxies/PROBE':return {'now':'saved-probe'}
            if path=='/providers/proxies':return {'providers':{'EOS':{'proxies':[{'name':'candidate','alive':True}]}}}
            raise AssertionError(path)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            def write(path,text):path.write_text(text)
            with patch.object(av,'transport',return_value=('bad','timeout')), \
                 patch.object(av,'generate',return_value=('quota','subscription_quota')):
                report=av.check_cli(root,controller,write)
        self.assertEqual(report['selection'],'old')
        self.assertEqual(report['status'],'quota')
        self.assertFalse(any(p=='/proxies/ANTIGRAVITY' for p,_ in mutations))
        self.assertEqual(mutations[-1],('/proxies/PROBE',{'name':'saved-probe'}))
