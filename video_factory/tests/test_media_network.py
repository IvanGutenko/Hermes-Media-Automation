import sys,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import media_network


class MediaNetworkProbeTests(unittest.TestCase):
    def test_google_reachability_alone_does_not_hide_failed_youtube(self):
        with patch.object(media_network.requests,'get',side_effect=[Mock(status_code=204),Mock(status_code=403,text='Access denied')]):
            self.assertFalse(media_network.probe(17893))
    def test_a_generic_html_page_is_not_a_valid_youtube_reply(self):
        with patch.object(media_network.requests,'get',side_effect=[Mock(status_code=204),Mock(status_code=200,text='Sign in to network')]):
            self.assertFalse(media_network.probe(17893))
    def test_both_labelled_service_responses_are_required(self):
        with patch.object(media_network.requests,'get',side_effect=[Mock(status_code=204),Mock(status_code=200,text='User-agent: *')]):
            self.assertTrue(media_network.probe(17893))


if __name__=='__main__':unittest.main()
