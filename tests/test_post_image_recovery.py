import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from post_images import request_attempts

class ImageRecovery(unittest.TestCase):
    def paths(self,folder,state,preview=True):
        original=folder/'pics0';original.mkdir(exist_ok=True)
        (original/'pics_receipt.json').write_text(json.dumps(state))
        if preview:(original/'export_ui.json').write_text(json.dumps({'snapshot':'radiogroup "Image previews"'}))
        return [p.name for p in request_attempts(folder,0)]

    def test_pending_and_completed_requests_never_authorize_a_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            for status in ('submitted','ready','completed','error','provider_unavailable'):
                self.assertEqual(self.paths(Path(tmp),{'status':status}),['pics0'])

    def test_status_alone_is_not_evidence_of_a_lost_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.paths(Path(tmp),{'status':'result_lost_before_confirmation'}),['pics0'])

    def test_observed_discarded_preview_allows_one_separate_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            state={'status':'result_lost_before_confirmation','result_loss_observed_at':123,
                   'result_loss_evidence':'Prior generated preview; restored empty loaded document'}
            self.assertEqual(self.paths(Path(tmp),state),['pics0','pics0_recovery1'])
            self.assertEqual(json.loads((Path(tmp)/'pics0/pics_receipt.json').read_text()),state)

    def test_observed_stale_empty_document_allows_only_one_preserved_recovery(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/'pics0';original.mkdir()
            snapshot='heading "Drop or select an image to start editing"'
            state={'status':'empty_document_after_submission','empty_observed_at':123,
                'document_url':'https://docs.google.com/images/d/example/edit',
                'empty_evidence_sha256':hashlib.sha256(snapshot.encode()).hexdigest()}
            (original/'pics_receipt.json').write_text(json.dumps(state))
            evidence=original/'empty_recovery_ui.json'
            evidence.write_text(json.dumps({'url':state['document_url'],'snapshot':snapshot}))
            self.assertEqual([p.name for p in request_attempts(root,0)],['pics0','pics0_recovery1'])
            evidence.write_text(json.dumps({'url':state['document_url'],'snapshot':'different'}))
            self.assertEqual([p.name for p in request_attempts(root,0)],['pics0'])

if __name__=='__main__':unittest.main()
