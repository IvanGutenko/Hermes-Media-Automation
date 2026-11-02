import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from video_quality import VisualReviewNeedsReview,require_pass


def verdict(**kw):
    base={'publishable':True,'score_1_to_5':4,'issues':[],
          'scenes':[{'id':1,'matches_narration':True,'meaningful_action_or_edit':True}]}
    base.update(kw);return base


class VisualVerdictTests(unittest.TestCase):
    def test_a_score_only_rejection_names_its_reason_instead_of_an_empty_string(self):
        with self.assertRaisesRegex(VisualReviewNeedsReview,'score 2/5 is below the 3/5 publication bar'):
            require_pass(verdict(score_1_to_5=2),1)

    def test_the_publication_bar_is_three_of_five_by_owner_decision(self):
        for score in (3,4,5):require_pass(verdict(score_1_to_5=score),1)
        for score in (2,1,0):
            with self.assertRaises(VisualReviewNeedsReview):require_pass(verdict(score_1_to_5=score),1)

    def test_a_three_still_needs_the_reviewer_to_call_it_publishable(self):
        with self.assertRaisesRegex(VisualReviewNeedsReview,'not publishable'):
            require_pass(verdict(score_1_to_5=3,publishable=False),1)

    def test_every_other_rejection_is_still_a_rejection(self):
        for bad in (verdict(publishable=False),verdict(scenes=[]),
                    verdict(scenes=[{'id':1,'matches_narration':False,'meaningful_action_or_edit':True}]),
                    verdict(scenes=[{'id':1,'matches_narration':True,'meaningful_action_or_edit':False}])):
            with self.assertRaises(VisualReviewNeedsReview):require_pass(bad,1)

    def test_reviewer_issues_are_reported_with_the_computed_reasons(self):
        with self.assertRaisesRegex(VisualReviewNeedsReview,'Frozen filler'):
            require_pass(verdict(publishable=False,issues=['Frozen filler']),1)

    def test_it_is_a_runtime_error_so_existing_handlers_still_catch_it(self):
        self.assertTrue(issubclass(VisualReviewNeedsReview,RuntimeError))


if __name__=='__main__':unittest.main()
