import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from reddit_publisher import post_id,blocks_new_submission,matches_uploaded_image,min_loaded_size,loaded_at_source_size


class RedditAcknowledgementTests(unittest.TestCase):
    def test_reddit_responsive_photo_is_identified_by_uploaded_media_id(self):
        photo={'src':'https://preview.redd.it/title-v0-9nm1l90qmjyh1.jpeg?width=640',
            'complete':True,'width':640,'height':357,'display_width':577,'display_height':322}
        self.assertTrue(matches_uploaded_image(photo,{'media_id':'9nm1l90qmjyh1'}))
        self.assertFalse(matches_uploaded_image(photo,{'media_id':'different'}))
        self.assertFalse(matches_uploaded_image({**photo,'complete':False},{'media_id':'9nm1l90qmjyh1'}))
        self.assertFalse(matches_uploaded_image({**photo,'src':'https://example.test/title-v0-9nm1l90qmjyh1.jpeg'},{'media_id':'9nm1l90qmjyh1'}))
    def test_failed_image_upload_before_post_does_not_block_other_posts(self):
        self.assertFalse(blocks_new_submission({'status':'uploading','last_error':'Image preview empty'}))
    def test_uncertain_publication_still_blocks_new_submissions(self):
        import time
        self.assertTrue(blocks_new_submission({'status':'publishing','publication_attempted_at':time.time()-60}))
        self.assertTrue(blocks_new_submission({'status':'uploading','id':'known-id'}))
    def test_an_uncertain_submission_stops_blocking_after_six_hours_but_is_never_resent(self):
        # 2026-11-01: one lost post id kept every newer Reddit post waiting; the owner wants continuous publishing.
        self.assertTrue(blocks_new_submission({'status':'publishing','publication_attempted_at':1000},now=1000+5*3600))
        self.assertFalse(blocks_new_submission({'status':'publishing','publication_attempted_at':1000},now=1000+7*3600))
        self.assertFalse(blocks_new_submission({'status':'abandoned_unverified','publication_attempted_at':1000},now=1001))
    def test_removed_post_is_terminal_and_does_not_block_new_approved_content(self):
        self.assertFalse(blocks_new_submission({'status':'rejected_by_platform','id':'removed-id','publication_attempted_at':1}))
    def test_profile_video_redirect_is_an_acknowledgement(self):
        self.assertEqual(post_id('https://www.reddit.com/user/ThinkScope/submitted/?created=t3_1xhlids&createdPostType=video&sort=hot'),'1xhlids')
    def test_readback_route_retains_the_same_id(self):
        self.assertEqual(post_id('https://www.reddit.com/user/ThinkScope/comments/1xhlids/'),'1xhlids')
    def test_profile_without_created_id_is_not_publication_proof(self):
        self.assertIsNone(post_id('https://www.reddit.com/user/ThinkScope/submitted/?sort=hot'))
    def test_other_domain_cannot_supply_a_publication_id(self):
        self.assertIsNone(post_id('https://example.test/comments/1xhlids/'))


class InsertedImageSizeTests(unittest.TestCase):
    def test_a_large_source_keeps_the_original_floor(self):
        self.assertEqual(min_loaded_size((1376,768)),(600,400))

    def test_a_small_screenshot_must_arrive_at_its_own_size(self):
        self.assertEqual(min_loaded_size((524,800)),(524,400))
        self.assertEqual(min_loaded_size((800,165)),(600,165))

    def test_a_correctly_loaded_small_screenshot_passes_and_a_placeholder_does_not(self):
        self.assertTrue(loaded_at_source_size({'width':524,'height':800},(524,800)))
        self.assertTrue(loaded_at_source_size({'width':1376,'height':768},(1376,768)))
        for source in ((1376,768),(524,800),(800,165)):
            self.assertFalse(loaded_at_source_size({'width':1,'height':1},source))
            self.assertFalse(loaded_at_source_size({'width':120,'height':80},source))
        self.assertFalse(loaded_at_source_size({'width':500,'height':800},(524,800)))
        self.assertFalse(loaded_at_source_size({'width':1376,'height':300},(1376,768)))

if __name__=='__main__':unittest.main()
