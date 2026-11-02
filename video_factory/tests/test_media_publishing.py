import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import youtube_uploader as y
import script_generator as s

class Response:
    def __init__(self,code=200,data=None,headers=None):
        self.status_code=code;self.data=data or {};self.headers=headers or {'content-type':'application/json'};self.ok=code<400
    def json(self):return self.data

class Session:
    def __init__(self,fail_verification=False):self.uploads=0;self.puts=0;self.fail=fail_verification
    def get(self,url,**kw):
        if url.endswith('/channels'):return Response(data={'items':[{'id':'channel','snippet':{'title':'test'}}]})
        if self.fail:raise RuntimeError('read back interrupted')
        return Response(data={'items':[{'id':'video','snippet':{'channelId':'channel','title':'test','publishedAt':'2026-10-27T12:08:19Z'},'status':{'privacyStatus':'public','uploadStatus':'processed'}}]})
    def post(self,*a,**kw):self.uploads+=1;return Response(headers={'Location':'https://upload.example/session'})
    def put(self,*a,**kw):
        self.puts+=1
        return Response(308) if self.puts==1 else Response(201,{'id':'video'})

class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name);self.video=self.p/'video.mp4';self.video.write_bytes(b'actual file identity')
    def tearDown(self):self.tmp.cleanup()
    def send(self,session,**kwargs):
        with patch.object(y,'get_session',return_value=session):
            return y.upload_video(self.video,'test','desc',privacy_status='public',expected_channel_id=kwargs.get('channel','channel'),receipt_path=self.p/'receipt.json')
    def test_repeated_upload_uses_persisted_video_id(self):
        session=Session();first=self.send(session);again=self.send(session)
        self.assertEqual(first['id'],again['id']);self.assertEqual(session.uploads,1);self.assertEqual(session.puts,2);self.assertEqual(again['status'],'success')
    def test_lost_readback_does_not_repeat_successful_upload(self):
        session=Session(True)
        with self.assertRaises(RuntimeError):self.send(session)
        self.assertEqual(json.loads((self.p/'receipt.json').read_text())['id'],'video')
        session.fail=False;self.send(session);self.assertEqual(session.uploads,1)
    def test_wrong_channel_rejected_before_upload(self):
        session=Session()
        with self.assertRaisesRegex(RuntimeError,'Wrong YouTube channel'):self.send(session,channel='other')
        self.assertEqual(session.uploads,0)
    def test_changed_file_cannot_reuse_receipt(self):
        session=Session();self.send(session);self.video.write_bytes(b'different video')
        with self.assertRaisesRegex(RuntimeError,'different file'):self.send(session)
        self.assertEqual(session.uploads,1)
    def test_brand_cta_does_not_leak_wrongdev(self):
        data={'scenes':[{'video_prompt_en':'A close up view of a passport lying on a desk beside a laptop displaying a government website. No people.',
             'voiceover_ru':'Проверьте документы перед поездкой','on_screen_text_ru':'Проверьте документы','edit_note_ru':'Приближение'} for _ in range(3)],
             'youtube_meta':{'title':'Что проверить перед поездкой','description':'Источник и подробности','tags':['переезд']}}
        with patch.dict(os.environ,MEDIA_BRAND='migratoria'):
            script=s.validate_script(data)
        self.assertIn('Мигратория',script['voiceover_ru']);self.assertNotIn('WrongDev',json.dumps(script))


if __name__=='__main__':unittest.main()
