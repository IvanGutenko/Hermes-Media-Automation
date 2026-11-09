"""Exercise the account gate used by both news and GrowModes publishers."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import content_distribution as d


class AccountCadence(unittest.TestCase):
    def test_shared_vk_group_spaces_videos_from_both_brands_without_delaying_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);(data/'distribution').mkdir()
            config=data/'brands.json'
            target={'enabled':True,'group_id':123,'video_account_interval_seconds':7200}
            config.write_text(json.dumps({brand:{'publishing':{'vk':target}} for brand in ('wrongdev','migratoria')}))
            payload={'source_id':'video-1','video':'master.mp4','title':'T','text':'D'}
            with patch.object(d,'DATA',data),patch.object(d,'CONFIG',config),patch.object(d.time,'time',return_value=10000) as clock,patch.object(d,'vk',return_value={'status':'published','url':'https://vk.ru/clip-123_1'}) as adapter,patch.object(d,'_announce'):
                d.publish('wrongdev','vk',payload)
                clock.return_value=17199
                self.assertEqual(d.publish('migratoria','vk',{**payload,'source_id':'video-2'})['retry_after'],17200)
                self.assertEqual(adapter.call_count,1)
                d.publish('migratoria','vk',{'source_id':'post-2','title':'T','text':'D'})
                self.assertEqual(adapter.call_count,2)
                clock.return_value=17200
                d.publish('migratoria','vk',{**payload,'source_id':'video-2'})
                self.assertEqual(adapter.call_count,3)

    def test_video_can_publish_after_two_hours_while_text_keeps_six_hour_account_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);(data/'distribution').mkdir()
            config=data/'brands.json'
            config.write_text(json.dumps({'lvl':{'publishing':{'instagram':{
                'enabled':True,'target':'growmodes','account_interval_seconds':21600,
                'video_account_interval_seconds':7200}}}}))
            (data/'distribution/instagram_growmodes_cursor.json').write_text(json.dumps({'source_id':'previous','last_post_at':10000}))
            payload={'source_id':'new','title':'T','text':'D'}
            with patch.object(d,'DATA',data),patch.object(d,'CONFIG',config),patch.object(d.time,'time',return_value=17199),patch.object(d,'instagram') as adapter:
                self.assertEqual(d.publish('lvl','instagram',{**payload,'video':'short.mp4'})['retry_after'],17200)
                adapter.assert_not_called()
            with patch.object(d,'DATA',data),patch.object(d,'CONFIG',config),patch.object(d.time,'time',return_value=17200),patch.object(d,'instagram',return_value={'status':'scheduled'}) as adapter:
                self.assertEqual(d.publish('lvl','instagram',payload)['retry_after'],31600)
                adapter.assert_not_called()
                d.publish('lvl','instagram',{**payload,'video':'short.mp4'})
                adapter.assert_called_once()


if __name__=='__main__':unittest.main()
