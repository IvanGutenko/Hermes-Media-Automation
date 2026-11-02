import sys,unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import browser_process


class BrowserDeadlineTests(unittest.TestCase):
    def test_deadline_runs_inside_container_before_host_timeout(self):
        cmd=['docker','exec','video_factory_auth','python3','worker.py']
        with patch.object(browser_process.subprocess,'run',return_value=Mock(returncode=124)) as run:
            result=browser_process.run(cmd,timeout=900,capture_output=True)
        args=run.call_args.args[0]
        self.assertEqual(args,['docker','exec','video_factory_auth','timeout','--kill-after=10s','870s','python3','worker.py'])
        self.assertEqual(run.call_args.kwargs['timeout'],900)
        self.assertEqual(result.returncode,124)

    def test_rejects_a_different_container(self):
        with self.assertRaises(ValueError):browser_process.run(['docker','exec','other'],timeout=60)


if __name__=='__main__':unittest.main()
