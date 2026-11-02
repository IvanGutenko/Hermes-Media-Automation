import hashlib,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import instagram_format as fmt
try:
    import content_distribution as distribution
except ModuleNotFoundError:
    distribution=None  # the browser container lacks the server's HTTP/Google libraries


class CaptionTests(unittest.TestCase):
    def test_sentences_with_links_or_mentions_are_dropped_whole(self):
        text=fmt.caption('Заголовок','Факт номер один важен. Подробности: https://t.me/WrongDevp и www.example.com.\n'
            'Все детали, разбор условий — в канале @WrongDevp.\n👉 @WrongDevp\nПишите @some.user, это важно. Факт два.')
        self.assertNotIn('http',text);self.assertNotIn('www',text);self.assertNotIn('@',text)
        self.assertNotIn('в канале',text);self.assertNotRegex(text,r'[:—]\s*$')
        self.assertIn('Факт номер один важен.',text);self.assertIn('Факт два.',text)

    def test_the_known_wrongdev_description_has_no_dangling_channel_phrase(self):
        text=fmt.caption('Anthropic сняла лимиты в Claude','Anthropic отменила 5-часовые лимиты. Все детали, переименование подписок Max и разбор условий — в канале @WrongDevp.')
        self.assertEqual(text,'Anthropic сняла лимиты в Claude\n\nAnthropic отменила 5-часовые лимиты.')

    def test_html_is_stripped_and_title_is_added_once(self):
        text=fmt.caption('Новый режим','<b>Новый режим</b> теперь доступен &amp; бесплатен')
        self.assertEqual(text.count('Новый режим'),1);self.assertIn('& бесплатен',text);self.assertNotIn('<',text)
        self.assertTrue(fmt.caption('Заголовок','Текст поста').startswith('Заголовок\n\nТекст'))

    def test_caption_is_limited_to_2200_characters_at_a_sentence_boundary(self):
        text=fmt.caption('T',('Это предложение. '*400))
        self.assertLessEqual(len(text),fmt.CAPTION_LIMIT);self.assertTrue(text.endswith('…'))
        self.assertIn('предложение.',text[-20:])

    def test_hashtags_beyond_thirty_are_dropped(self):
        text=fmt.caption('T',' '.join('#t%d'%i for i in range(40)))
        self.assertEqual(len(__import__('re').findall(r'#\w+',text)),30)

    def test_an_empty_caption_is_refused(self):
        with self.assertRaises(ValueError):fmt.caption('','https://example.com @x')


class ChannelLinkTests(unittest.TestCase):
    LINK='https://t.me/WrongDevp'

    def test_the_owners_channel_link_closes_the_caption_and_foreign_links_stay_out(self):
        text='Факт номер один. Подробности: https://example.com/x и @someone.\nПодробности и ссылки в @WrongDevp и https://t.me/WrongDevp.'
        result=fmt.caption('Заголовок',text,self.LINK)
        self.assertTrue(result.endswith('\n\nПодробности и ссылки — в Telegram: '+self.LINK))
        self.assertEqual(result.count('http'),1);self.assertNotIn('@',result);self.assertNotIn('example',result)

    def test_without_a_link_the_caption_is_unchanged(self):
        text='Факт номер один. Ещё факт.'
        self.assertEqual(fmt.caption('Заголовок',text),fmt.caption('Заголовок',text,None))
        self.assertNotIn('http',fmt.caption('Заголовок',text))

    def test_a_long_caption_is_shortened_before_the_link_never_through_it(self):
        text=' '.join('Предложение номер %d про переезд и документы.'%i for i in range(200))
        result=fmt.caption('Заголовок',text,self.LINK)
        self.assertLessEqual(len(result),fmt.CAPTION_LIMIT)
        self.assertTrue(result.endswith(self.LINK));self.assertIn('…\n\n'+fmt.LINK_LINE,result)

    def test_the_link_is_not_duplicated_when_hashtags_end_the_text(self):
        result=fmt.caption('Заголовок','Текст.\n#travel #uzbekistan',self.LINK)
        self.assertEqual(result.count(self.LINK),1);self.assertTrue(result.endswith(self.LINK))


class MediaRuleTests(unittest.TestCase):
    def test_feed_photo_range(self):
        self.assertIsNone(fmt.photo_target(1376,768));self.assertIsNone(fmt.photo_target(1080,1350))
        self.assertEqual(fmt.photo_target(524,800),fmt.PORTRAIT);self.assertEqual(fmt.photo_target(2000,500),fmt.LANDSCAPE)
        self.assertIsNone(fmt.fit_filter(1376,768))
        self.assertIn('1080:1350',fmt.fit_filter(524,800))

    def test_reel_needs_vertical_video_of_allowed_length(self):
        self.assertIsNone(fmt.reel_problem(1080,1920,45))
        self.assertTrue(fmt.reel_problem(1920,1080,45));self.assertTrue(fmt.reel_problem(1080,1920,1))
        self.assertTrue(fmt.reel_problem(1080,1920,400))


class FrameRuleTests(unittest.TestCase):
    def test_the_default_square_crop_of_a_reel_is_a_problem_and_the_whole_frame_is_not(self):
        self.assertIn('crop',fmt.frame_problem((713,713),(720,1280)))
        self.assertIsNone(fmt.frame_problem((401,713),(720,1280)))
        self.assertIsNone(fmt.frame_problem((713,401),(1280,720)))
        self.assertIn('crop',fmt.frame_problem((713,713),(1080,1350)))
        self.assertIsNone(fmt.frame_problem((570,713),(1080,1350)))

    def test_a_preview_that_cannot_be_measured_is_not_accepted_as_whole(self):
        for window in (None,(0,713),(401,0)):self.assertIn('measured',fmt.frame_problem(window,(720,1280)))
        self.assertIn('measured',fmt.frame_problem((401,713),None))

    def test_small_rounding_differences_are_tolerated_but_a_letterboxed_or_zoomed_frame_is_not(self):
        self.assertIsNone(fmt.frame_problem((400,713),(720,1280)))
        self.assertIsNotNone(fmt.frame_problem((450,713),(720,1280)))
        self.assertIsNotNone(fmt.frame_problem((401,640),(720,1280)))


class ReceiptRuleTests(unittest.TestCase):
    def test_a_post_that_may_be_public_is_never_uploaded_again(self):
        self.assertTrue(fmt.may_resubmit({'status':'new'}));self.assertTrue(fmt.may_resubmit({'status':'uploading'}))
        for status in ('publishing','posted_unverified','share_failed_visible','published'):
            self.assertFalse(fmt.may_resubmit({'status':status,'share_attempted_at':1}),status)
        self.assertFalse(fmt.may_resubmit({'status':'uploading','share_attempted_at':1}))

    def test_only_an_attempted_share_blocks_other_submissions(self):
        self.assertTrue(fmt.blocks_new_submission({'status':'posted_unverified','share_attempted_at':1}))
        self.assertFalse(fmt.blocks_new_submission({'status':'uploading','last_error':'x'}))
        self.assertFalse(fmt.blocks_new_submission({'status':'published','share_attempted_at':1}))

    def test_new_profile_entry_is_found_by_difference_and_foreign_links_are_ignored(self):
        hrefs=['/me/p/AAA/','/me/reel/BBB/','/p/CCC/','/other/p/ZZZ/','https://example.test/p/EVIL/','/me/p/AAA/']
        codes=fmt.profile_codes(hrefs,'me')
        self.assertEqual(codes,['AAA','BBB','CCC']);self.assertEqual(fmt.new_codes(['AAA','BBB'],codes),['CCC'])
        self.assertEqual(fmt.permalink('CCC','video'),'https://www.instagram.com/reel/CCC/')

    def test_caption_match_is_complete_not_partial(self):
        self.assertTrue(fmt.caption_visible('x\n\nОдна  строка\nдве','Одна строка две'))
        self.assertFalse(fmt.caption_visible('Одна строка','Одна строка две'))


@unittest.skipIf(distribution is None,'runs on the server host')
class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.out=root/'output';self.out.mkdir();self.data=root/'data';self.data.mkdir()
        self.config=self.data/'brands.json'
        self.root=root

    def tearDown(self):self.tmp.cleanup()

    def settings(self,**values):
        self.config.write_text(json.dumps({'wrongdev':{'publishing':{'instagram':{'enabled':False,'target':None,**values}}}}))
        return [patch.object(distribution,'BASE_DIR',self.root),patch.object(distribution,'DATA',self.data),patch.object(distribution,'CONFIG',self.config)]

    def run_publish(self,payload,patches,**extra):
        for p in patches:p.start()
        try:return distribution.publish('wrongdev','instagram',payload)
        finally:
            for p in patches:p.stop()

    def test_disabled_account_needs_authorization_and_touches_no_browser(self):
        with patch.object(distribution,'run_browser') as browser:
            result=self.run_publish({'source_id':'post-1','title':'T','text':'x'},self.settings())
        self.assertEqual(result,{'status':'needs_authorization','platform':'instagram'});browser.assert_not_called()

    def test_enabled_account_publishes_once_and_enforces_the_account_interval(self):
        media=self.out/'runs'/'r1'/'short.mp4';media.parent.mkdir(parents=True);media.write_bytes(b'video')
        payload={'source_id':'video-r1','title':'Title','text':'Body https://x.test','video':str(media)}
        patches=self.settings(enabled=True,target='thinkscope.hub',account_interval_seconds=1000)
        key=hashlib.sha256(json.dumps({'brand':'wrongdev','target':'thinkscope.hub','source':'video-r1','kind':'video'},sort_keys=True).encode()).hexdigest()
        sha=hashlib.sha256(b'video').hexdigest()
        proof={'status':'published','username':'thinkscope.hub','source_id':'video-r1','sha256':sha,'code':'ABC123',
            'url':'https://www.instagram.com/reel/ABC123/','published_at':100,'verified_at':120}
        (self.out/('native_instagram_'+key+'.json')).write_text(json.dumps(proof))
        browser=Mock(return_value=Mock(returncode=0,stdout=json.dumps({'status':'published','url':proof['url']}),stderr=''))
        for p in patches:p.start()
        try:
            with patch.object(distribution,'run_browser',browser),patch.object(distribution.time,'time',return_value=500):
                published=distribution.publish('wrongdev','instagram',payload)
                later=distribution.publish('wrongdev','instagram',{**payload,'source_id':'video-r2'})
        finally:
            for p in patches:p.stop()
        self.assertEqual(published['status'],'published');self.assertEqual(published['url'],proof['url'])
        job=json.loads((self.out/('instagram_native_job_'+key+'.json')).read_text())
        self.assertEqual(job['username'],'thinkscope.hub');self.assertNotIn('http',job['caption'])
        self.assertEqual(job['video'],'/data/output/runs/r1/short.mp4');self.assertIsNone(job['image'])
        self.assertTrue(job['ai_label'])
        self.assertEqual(later['status'],'scheduled');self.assertEqual(later['retry_after'],1100)
        self.assertEqual(browser.call_count,1)

    def channel_payload(self,config,platform,**payload):
        self.config.write_text(json.dumps(config))
        patches=[patch.object(distribution,'BASE_DIR',self.root),patch.object(distribution,'DATA',self.data),patch.object(distribution,'CONFIG',self.config)]
        browser=Mock(return_value=Mock(returncode=1,stdout='',stderr='stop here'))
        for p in patches:p.start()
        try:
            with patch.object(distribution,'run_browser',browser),self.assertRaises(RuntimeError):
                distribution.publish('wrongdev',platform,{'source_id':'video-r1','title':'Title','text':'Body https://x.test.',**payload})
        finally:
            for p in patches:p.stop()
        jobs=list(self.out.glob(platform+'_native_job_*.json'))
        return json.loads(jobs[0].read_text()) if jobs else None

    def test_instagram_and_tiktok_videos_carry_the_brands_telegram_link(self):
        media=self.out/'runs'/'r1'/'short.mp4';media.parent.mkdir(parents=True);media.write_bytes(b'video')
        for platform in ('instagram','tiktok'):
            with self.subTest(platform=platform):
                for old in self.out.glob(platform+'_native_job_*.json'):old.unlink()
                job=self.channel_payload({'wrongdev':{'telegram':'@WrongDevp','publishing':{platform:{'enabled':True,'target':'acct','account_interval_seconds':0}}}},
                    platform,video=str(media))
                self.assertTrue(job['caption'].endswith('Подробности и ссылки — в Telegram: https://t.me/WrongDevp'))
                self.assertEqual(job['caption'].count('http'),1)

    def test_a_brand_without_a_channel_gets_no_link_and_a_photo_post_is_left_alone(self):
        media=self.out/'runs'/'r1'/'short.mp4';media.parent.mkdir(parents=True);media.write_bytes(b'video')
        job=self.channel_payload({'wrongdev':{'publishing':{'instagram':{'enabled':True,'target':'acct','account_interval_seconds':0}}}},
            'instagram',video=str(media))
        self.assertNotIn('http',job['caption'])
        for old in self.out.glob('instagram_native_job_*.json'):old.unlink()
        image=self.out/'photo.jpg';image.write_bytes(b'img')
        with patch('instagram_adaptation.prepare_image',side_effect=lambda src,key:src):
            job=self.channel_payload({'wrongdev':{'telegram':'@WrongDevp','publishing':{'instagram':{'enabled':True,'target':'acct','account_interval_seconds':0}}}},
                'instagram',image=str(image),source_id='post-5')
        self.assertNotIn('http',job['caption'])

    def test_ai_label_follows_the_image_origin(self):
        (self.out/'post_images/source').mkdir(parents=True);(self.out/'post_images/gen').mkdir(parents=True)
        real=self.out/'post_images/source/shot.jpg';made=self.out/'post_images/gen/image.jpg'
        real.write_bytes(b'real');made.write_bytes(b'made')
        patches=self.settings(enabled=True,target='thinkscope.hub',account_interval_seconds=0)
        labels={}
        browser=Mock(return_value=Mock(returncode=1,stdout='',stderr='stop here'))
        for p in patches:p.start()
        try:
            for name,path in (('real',real),('made',made)):
                with patch.object(distribution,'run_browser',browser),patch('instagram_adaptation.prepare_image',side_effect=lambda src,key:src):
                    with self.assertRaises(RuntimeError):
                        distribution.publish('wrongdev','instagram',{'source_id':'post-'+name,'title':'T','text':'Body.','image':str(path)})
        finally:
            for p in patches:p.stop()
        for job in (self.out).glob('instagram_native_job_*.json'):
            data=json.loads(job.read_text());labels[data['source_id']]=data['ai_label']
        self.assertEqual(labels,{'post-real':False,'post-made':True})

    def test_a_receipt_for_another_account_or_source_is_not_accepted_as_proof(self):
        media=self.out/'short.mp4';media.write_bytes(b'video')
        payload={'source_id':'video-r1','title':'Title','text':'Body','video':str(media)}
        patches=self.settings(enabled=True,target='thinkscope.hub')
        key=hashlib.sha256(json.dumps({'brand':'wrongdev','target':'thinkscope.hub','source':'video-r1','kind':'video'},sort_keys=True).encode()).hexdigest()
        proof={'status':'published','username':'someone.else','source_id':'video-r1','sha256':hashlib.sha256(b'video').hexdigest(),
            'code':'ABC','url':'https://www.instagram.com/reel/ABC/'}
        (self.out/('native_instagram_'+key+'.json')).write_text(json.dumps(proof))
        browser=Mock(return_value=Mock(returncode=0,stdout='{}',stderr=''))
        for p in patches:p.start()
        try:
            with patch.object(distribution,'run_browser',browser),self.assertRaises(RuntimeError):
                distribution.publish('wrongdev','instagram',payload)
        finally:
            for p in patches:p.stop()


if __name__=='__main__':unittest.main()
