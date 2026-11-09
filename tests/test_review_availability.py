import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import media_review_client as review

class ReviewAvailability(unittest.TestCase):
    def test_region_refusal_stops_api_requests_and_uses_approved_provider(self):
        response=Mock(ok=False,status_code=400)
        response.json.return_value={'error':{'status':'FAILED_PRECONDITION','message':'User location is not supported for the API use.'}}
        actual={'relevant':True,'usable':True}
        with tempfile.TemporaryDirectory() as tmp,patch.dict(review.os.environ,GEMINI_API_KEY='test'),\
                patch.object(review.requests,'post',return_value=response) as post,\
                patch.object(review,'agy_media_review',return_value=(actual,'approved-provider')) as alternative:
            result,model=review.review([{'text':'Inspect actual supplied image'}],Path(tmp)/'proof.json')
        self.assertIs(result,actual);self.assertEqual(model,'approved-provider')
        self.assertEqual(post.call_count,1);alternative.assert_called_once()

    def test_invalid_request_and_permission_denial_remain_failures(self):
        for code,status in ((400,'INVALID_ARGUMENT'),(401,'UNAUTHENTICATED'),(403,'PERMISSION_DENIED')):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as tmp:
                response=Mock(ok=False,status_code=code)
                response.json.return_value={'error':{'status':status,'message':'Rejected'}}
                with patch.dict(review.os.environ,GEMINI_API_KEY='test'),\
                        patch.object(review.requests,'post',return_value=response),\
                        patch.object(review,'agy_media_review') as alternative:
                    with self.assertRaises(RuntimeError):review.review([],Path(tmp)/'proof.json')
                alternative.assert_not_called()

if __name__=='__main__':unittest.main()
