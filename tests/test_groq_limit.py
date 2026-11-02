import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/'plugins/approved-providers/groq_client.py'
spec=importlib.util.spec_from_file_location('groq_capacity',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class GroqCapacityTests(unittest.TestCase):
    def test_per_minute_capacity_is_not_reported_as_context_overflow(self):
        body={'error':{'code':'rate_limit_exceeded',
                      'message':'Request too large on tokens per minute (TPM): Limit 8000, Requested 14678'}}
        self.assertTrue(module.is_tpm_capacity_error(413,body))
        self.assertFalse(module.is_tpm_capacity_error(400,body))
        self.assertFalse(module.is_tpm_capacity_error(413,{'error':'Payload too large'}))
        self.assertFalse(module.is_tpm_capacity_error(413,{'error':{'code':'context_length_exceeded',
                                            'message':'Request too large for context length'}}))
