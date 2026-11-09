import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import growth_daily_report as report


class Report(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher=patch.object(report.ga,'BASE_DIR',self.root);patcher.start();self.addCleanup(patcher.stop)
        self.events=[{'brand':'wrongdev','consumer':'video_script','run_id':'real','at':1794047450,'revision':'old','actions':['early_payoff']},
            {'brand':'wrongdev','consumer':'source_selection','source_id':1,'at':1794047450,'revision':'new'}]
        self.snapshot={'effective_status':'active','measurement_age_seconds':120,'state':{'revision':'new','measurements_at':1794047450,
            'brands':{'wrongdev':{'actions':[],'allocations':{},'protected_experiment_factors':['hook']}}},
            'experiments':[{'id':'one','assigned_test':13,'assigned_control':12,'n_test':4,'n_control':3,'need_per_arm':8,'decision':'collecting'}],
            'recent_consumption':self.events}

    def receipt(self, digest=None):
        root=self.root/'output/runs/real';root.mkdir(parents=True)
        (root/'short.mp4').write_bytes(b'published bytes')
        value={'status':'published','publication':{'status':'success','url':'https://youtu.be/example',
            'sha256':digest or hashlib.sha256(b'published bytes').hexdigest()}}
        (root/'manifest.json').write_text(json.dumps(value))

    def test_a_script_or_source_event_never_counts_as_a_publication(self):
        self.assertEqual(report.publication_receipts(self.events),[])

    def test_success_receipt_requires_the_exact_published_video_bytes(self):
        self.receipt(digest='wrong')
        self.assertEqual(report.publication_receipts(self.events),[])

    def test_duplicate_events_for_one_published_run_are_counted_once(self):
        self.receipt()
        self.assertEqual(len(report.publication_receipts(self.events+self.events)),1)

    def test_report_preserves_nonzero_maturity_and_separates_old_actions_from_current(self):
        target=self.root/'reports'
        with patch.object(report.ga,'status',return_value=self.snapshot):brief=report.write_report(target)
        text=next(target.glob('*.md')).read_text()
        evidence=json.loads(next(target.glob('*.json')).read_text())
        self.assertIn('13/12',text);self.assertIn('4/3',text)
        self.assertIn('Дополнительных активных сценарных действий нет',text)
        self.assertIn('Исторические actions',text)
        self.assertIn('7 зрелых замеров',brief);self.assertIn('подтверждено 0 квитанций',brief)
        self.assertEqual(evidence['state']['brands']['wrongdev']['actions'],[])
        self.assertIn('Статус всех площадок этим отчётом не проверяется',text)

    def test_stale_snapshot_is_never_described_as_applied_actions(self):
        self.snapshot['effective_status']='waiting_for_fresh_measurements'
        with patch.object(report.ga,'status',return_value=self.snapshot):brief=report.write_report(self.root/'reports')
        self.assertIn('Срез устарел: правки из него не применяются',next((self.root/'reports').glob('*.md')).read_text())
        self.assertIn('правки из этого среза не применяются',brief)
        self.assertNotIn('передаются производству',brief)


if __name__=='__main__':unittest.main()
