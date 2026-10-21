"""Final evaluation denominators and fixed input coverage; no model or user DB."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('final_evaluator',ROOT/'eval/run_final_eval.py')
evaluator=importlib.util.module_from_spec(spec);spec.loader.exec_module(evaluator)

class FinalEvaluationTests(unittest.TestCase):
    def test_empty_denominator_is_unmeasured(self):
        self.assertIsNone(evaluator.ratio(0,0)['rate'])

    def test_wrong_blocked_and_correct_blocked_are_separate(self):
        summary=evaluator.summarize([
            {'group':'validator','expected':'reject','passed':True},
            {'group':'validator','expected':'reject','passed':False},
            {'group':'validator','expected':'accept','passed':True},
            {'group':'validator','expected':'accept','passed':False}])
        self.assertEqual(summary['incorrect_blocked'],{'passed':1,'total':2,'rate':0.5})
        self.assertEqual(summary['correct_blocked'],{'passed':1,'total':2,'rate':0.5})

    def test_fixed_cases_cover_six_manuals_and_missing_source(self):
        cases=json.loads((ROOT/'eval/cases.json').read_text())
        self.assertEqual({c['expected_runbooks'][0] for c in cases if c['id'].startswith('type-')},
                         {p.stem for p in (ROOT/'data/runbooks').glob('*.md')})
        self.assertTrue(any(c.get('missing_documents') for c in cases))

if __name__=='__main__':unittest.main()
