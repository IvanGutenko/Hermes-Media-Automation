import re,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import browser_proxy_bridge as bridge


def routed():
    body=bridge.PAC.decode()
    block=re.search(r'var domains = \[(.*?)\];',body,re.S).group(1)
    block=re.sub(r'//[^\n]*','',block)
    return set(re.findall(r'"([^"]+)"',block))


class PacTests(unittest.TestCase):
    def test_google_and_youtube_stay_on_the_egress(self):
        for domain in ('google.com','googleapis.com','labs.google','youtube.com','ytimg.com','googlevideo.com'):
            self.assertIn(domain,routed())

    def test_instagram_and_its_cdns_use_the_egress(self):
        for domain in ('instagram.com','cdninstagram.com','fbcdn.net','facebook.com','facebook.net'):
            self.assertIn(domain,routed())

    def test_tiktok_and_its_cdns_use_the_egress(self):
        for domain in ('tiktok.com','tiktokv.com','tiktokcdn.com','ttwstatic.com','byteoversea.com'):
            self.assertIn(domain,routed())

    def test_other_services_stay_direct(self):
        for domain in ('reddit.com','redd.it','vk.ru','vk.com','dzen.ru','t.me','telegram.org'):
            self.assertNotIn(domain,routed())

    def test_pac_is_served_without_unbalanced_lists(self):
        body=bridge.PAC.decode()
        self.assertEqual(body.count('['),body.count(']'))
        self.assertIn('return "DIRECT"',body)


if __name__=='__main__':unittest.main()
