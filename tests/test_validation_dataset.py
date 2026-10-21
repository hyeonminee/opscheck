import asyncio
import copy
import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'eval')]
from run_validation import load_dataset, run_all, assess

class ValidationDatasetTests(unittest.TestCase):
    def test_source_integrity_and_coverage(self):
        data=load_dataset();cases=data['cases']
        self.assertEqual(len(cases),39)
        for category in ('single_target','multiple_targets','normal','target_confirmation','unregistered','general_chat','followup','partial_failure','insufficient_data','bad_draft','correct_draft','phase9','phase10'):
            self.assertIn(category,{c['category'] for c in cases})
        queries={t['question'] for c in cases for t in c.get('turns',[])}
        legacy=json.loads((ROOT/'eval/cases.json').read_text())
        self.assertTrue({c['query'] for c in legacy}<=queries)
        for case in cases:
            self.assertTrue(case['ground_truth_basis'])
            for t in case.get('turns',[case]):
                self.assertTrue(t['pass_criteria']);self.assertTrue(t['fail_criteria']);self.assertTrue(t['example'])
                self.assertTrue(t.get('expected'))

    def test_all_fixed_oracles(self):
        results=asyncio.run(run_all())
        self.assertEqual([r for r in results if r['status']=='failed'],[])
        self.assertEqual(sum(r['status']=='passed' for r in results),35)
        self.assertEqual(sum(r['status']=='linked_test' for r in results),4)

    def test_http_success_or_nonempty_answer_is_not_enough(self):
        case=next(c for c in load_dataset()['cases'] if c['id']=='payments-single')
        class MCP: calls=['auth-service']
        fake={'route':'incident','final':'그럴듯한 답변','processing_status':'completed','validation_status':'passed'}
        checks=assess(fake,MCP(),case['turns'][0]['expected'])
        self.assertFalse(all(checks.values()))
        self.assertFalse(checks['facts']);self.assertFalse(checks['sources']);self.assertFalse(checks['looked_up_targets'])

if __name__=='__main__':unittest.main()
