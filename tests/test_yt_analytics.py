"""YouTube Analytics API client: consent exchange, per-video overview, and no secrets in output or errors."""
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import yt_analytics as ya

NOW = 1_800_000_000
PUBLISHED = NOW - 40 * 3600


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'client_secret.json').write_text(json.dumps({'installed': {'client_id': 'cid', 'client_secret': 'csecret'}}))
        for name, value in [('SECRET', root / 'client_secret.json'), ('TOKENS', root / 'data/yt_analytics')]:
            p = patch.object(ya, name, value)
            p.start()
            self.addCleanup(p.stop)
        ya._cache.clear()
        self.root = root


class ConsentTests(Base):
    def test_consent_url_asks_for_readonly_scopes_only_and_every_channel_choice(self):
        url = ya.consent_url()
        self.assertIn('yt-analytics.readonly', url)
        self.assertIn('prompt=consent+select_account', url)
        self.assertNotIn('upload', url)
        self.assertNotIn('csecret', url)

    def test_redirect_parsing(self):
        self.assertEqual(ya.parse_redirect('http://localhost/?code=4%2Fabc&scope=x'), '4/abc')
        with self.assertRaisesRegex(ya.AnalyticsError, 'refused'):ya.parse_redirect('http://localhost/?error=access_denied')
        with self.assertRaisesRegex(ya.AnalyticsError, 'no code'):ya.parse_redirect('http://localhost/')

    def exchange_with(self, granted, channels):
        def fake_post(fields):
            self.assertEqual(fields['grant_type'], 'authorization_code')
            self.assertEqual(fields['redirect_uri'], 'http://localhost')
            return granted
        with patch.object(ya, '_post', side_effect=fake_post), patch.object(ya, '_get', return_value={'items': channels}):
            return ya.exchange('http://localhost/?code=c')

    def test_exchange_stores_a_private_token_file_and_returns_no_secret(self):
        result = self.exchange_with({'access_token': 'AT', 'refresh_token': 'RT', 'scope': ' '.join(ya.SCOPES)},
                                    [{'id': 'UC1', 'snippet': {'title': 'WrongDev'}}])
        self.assertEqual(result, {'channel_id': 'UC1', 'channel_title': 'WrongDev', 'scopes': list(ya.SCOPES)})
        path = ya.TOKENS / 'UC1.json'
        self.assertEqual(oct(path.stat().st_mode & 0o777), '0o600')
        self.assertEqual(json.loads(path.read_text())['refresh_token'], 'RT')
        self.assertNotIn('RT', json.dumps(result))
        self.assertEqual(ya.channels(), ['UC1'])

    def test_missing_scope_missing_refresh_token_or_ambiguous_channel_are_refused(self):
        with self.assertRaisesRegex(ya.AnalyticsError, 'not granted'):
            self.exchange_with({'access_token': 'AT', 'refresh_token': 'RT', 'scope': ya.SCOPES[0]}, [])
        with self.assertRaisesRegex(ya.AnalyticsError, 'no refresh token'):
            self.exchange_with({'access_token': 'AT', 'scope': ' '.join(ya.SCOPES)}, [])
        with self.assertRaisesRegex(ya.AnalyticsError, 'got 2'):
            self.exchange_with({'access_token': 'AT', 'refresh_token': 'RT', 'scope': ' '.join(ya.SCOPES)}, [{'id': 'a', 'snippet': {'title': 'a'}}, {'id': 'b', 'snippet': {'title': 'b'}}])
        self.assertEqual(ya.channels(), [])

    def test_http_errors_carry_google_s_message_but_never_the_request(self):
        body = io.BytesIO(json.dumps({'error': 'invalid_grant', 'error_description': 'Bad Request'}).encode())
        error = urllib.error.HTTPError(ya.TOKEN_URL, 400, 'Bad', {}, body)
        with patch.object(ya.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(ya.AnalyticsError) as caught:ya._post({'code': 'SECRETCODE', 'client_secret': 'csecret'})
        self.assertIn('invalid_grant', str(caught.exception))
        for secret in ('SECRETCODE', 'csecret'):self.assertNotIn(secret, str(caught.exception))


class OverviewTests(Base):
    def setUp(self):
        super().setUp()
        ya.TOKENS.mkdir(parents=True)
        (ya.TOKENS / 'UC1.json').write_text(json.dumps({'refresh_token': 'RT'}))

    def run_overview(self, main_rows, source_rows, source_error=False):
        calls = []

        def fake_get(url, params, token):
            calls.append(params)
            self.assertEqual(token, 'AT')
            if 'dimensions' in params:
                if source_error:raise ya.AnalyticsError('HTTP 400: unsupported')
                return {'columnHeaders': [{'name': 'insightTrafficSourceType'}, {'name': 'views'}], 'rows': source_rows}
            return {'columnHeaders': [{'name': n} for n in ('views', 'engagedViews', 'averageViewDuration', 'averageViewPercentage', 'subscribersGained', 'likes', 'comments', 'shares')],
                    'rows': main_rows}
        with patch.object(ya, '_post', return_value={'access_token': 'AT', 'expires_in': 3600}), patch.object(ya, '_get', side_effect=fake_get):
            return ya.video_overview('UC1', 'vid', PUBLISHED, NOW), calls

    def test_overview_has_the_studio_keys_the_api_can_fill(self):
        result, calls = self.run_overview([[1000, 640, 17.6, 58.34, 3, 40, 2, 1]], [['SHORTS', 800], ['YT_SEARCH', 150], ['SUBSCRIBER', 50]])
        self.assertEqual(result['views'], 1000)
        self.assertEqual(result['average_view_duration_s'], 18)
        self.assertEqual(result['average_percentage_viewed'], 58.3)
        self.assertEqual(result['subscribers_gained'], 3)
        self.assertEqual(result['engaged_views_pct'], 64.0)
        self.assertEqual(result['traffic_sources'], {'Shorts feed': 80.0, 'YouTube search': 15.0, 'Browse features': 5.0})
        self.assertNotIn('stayed_to_watch_pct', result)   # no API metric: never invented
        self.assertEqual(result['retention_source'], 'YouTube Analytics API')
        self.assertEqual(result['overview_observed_at'], NOW)
        self.assertEqual(calls[0]['filters'], 'video==vid')
        self.assertEqual(calls[0]['ids'], 'channel==MINE')
        self.assertEqual(calls[0]['startDate'], time.strftime('%Y-%m-%d', time.gmtime(PUBLISHED)))

    def test_no_row_yet_gives_an_empty_result_not_zeros(self):
        result, _ = self.run_overview([], [])
        self.assertEqual(result, {})

    def test_a_failing_traffic_report_keeps_the_retention_numbers(self):
        result, _ = self.run_overview([[10, 5, 9.0, 40.0, 0, 0, 0, 0]], [], source_error=True)
        self.assertEqual(result['subscribers_gained'], 0)
        self.assertNotIn('traffic_sources', result)

    def test_access_tokens_are_reused_until_they_expire(self):
        with patch.object(ya, '_post', return_value={'access_token': 'AT', 'expires_in': 3600}) as post:
            ya.access_token('UC1', NOW)
            ya.access_token('UC1', NOW + 100)
            self.assertEqual(post.call_count, 1)
            ya.access_token('UC1', NOW + 3600)
            self.assertEqual(post.call_count, 2)


if __name__ == '__main__':
    unittest.main()
