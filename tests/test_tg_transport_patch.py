import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from patch_tg_transport import apply

class Transport(unittest.TestCase):
    def test_distinct_polling_and_send_clients_are_installed_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'main.py'
            original='class App:\n    def run(self):\n        request = HTTPXRequest(proxy=config.proxy, connect_timeout=15.0, read_timeout=30.0)\n        app = builder.get_updates_request(request)\n'
            p.write_text(original)
            self.assertTrue(apply(root));self.assertFalse(apply(root))
            result=p.read_text()
            self.assertIn('.get_updates_request(polling_request)',result)
            self.assertIn('connection_pool_size=8',result)
            self.assertEqual((root/'main.before-transport-20261108.py').read_text(),original)

if __name__=='__main__':unittest.main()
