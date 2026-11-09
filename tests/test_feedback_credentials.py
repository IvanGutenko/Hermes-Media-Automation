"""A revoked upload grant must not disable counters readable through a valid grant."""
import sys,unittest,tempfile
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import content_feedback as feedback
import yt_analytics
import youtube_uploader

class FeedbackCredentials(unittest.TestCase):
    def test_readonly_grant_supplies_real_counter_session_without_upload_grant(self):
        with patch.object(yt_analytics,'channels',return_value=['own-channel']),\
                patch.object(yt_analytics,'access_token',return_value='test-read-token'),\
                patch.object(youtube_uploader,'get_session') as upload:
            session=feedback.youtube_session({})
        self.assertEqual(session.headers['Authorization'],'Bearer test-read-token')
        upload.assert_not_called()
        session.close()

    def test_invalid_readonly_grant_falls_back_to_existing_upload_grant(self):
        with tempfile.TemporaryDirectory() as folder:
            token=Path(folder)/'grant.json';token.write_text('{}');expected=Mock()
            with patch.object(yt_analytics,'channels',return_value=['revoked']),\
                    patch.object(yt_analytics,'access_token',side_effect=RuntimeError('revoked')),\
                    patch.object(youtube_uploader,'get_session',return_value=expected):
                result=feedback.youtube_session({'brand':{'youtube_token':str(token)}})
        self.assertIs(result,expected)

if __name__=='__main__':unittest.main()
