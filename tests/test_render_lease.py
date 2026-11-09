"""An encoder and browser publisher must not own the same media lease together."""
import fcntl
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from browser_process import render_lease,lease_file


class RenderLeaseTests(unittest.TestCase):
    def test_browser_is_excluded_and_the_lease_is_released_on_render_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);(base/'output').mkdir()
            with self.assertRaisesRegex(RuntimeError,'encoder failed'):
                with render_lease(base):
                    with lease_file(base).open('r') as contender:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(contender,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    raise RuntimeError('encoder failed')
            with lease_file(base).open('r') as contender:
                fcntl.flock(contender,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_continuous_publisher_demand_cannot_starve_rendering(self):
        from unittest.mock import patch
        import browser_process as bp
        now=[0.0]
        def sleep(seconds):now[0]+=seconds
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);(base/'output').mkdir()
            with patch.object(bp,'publisher_waiting',return_value=True),patch.object(bp.time,'monotonic',side_effect=lambda:now[0]),patch.object(bp.time,'sleep',side_effect=sleep):
                with render_lease(base):
                    self.assertGreaterEqual(now[0],bp.WAITING_FRESH_SECONDS)
                    self.assertLess(now[0],bp.WAITING_FRESH_SECONDS+3)
                    with lease_file(base).open('r') as contender:
                        with self.assertRaises(BlockingIOError):fcntl.flock(contender,fcntl.LOCK_EX|fcntl.LOCK_NB)
