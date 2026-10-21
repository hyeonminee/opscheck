import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'eval'))
os.environ.update(LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
from harness import run_case
from agent import graph


class BaselineChecks(unittest.IsolatedAsyncioTestCase):
    def case(self, name):
        return next(c for c in json.loads((ROOT / 'eval/cases.json').read_text()) if c['id'] == name)

    async def test_chat_does_not_investigate(self):
        self.assertTrue((await run_case(self.case('greeting'), ROOT))['passed'])

    async def test_fixed_history_remembers_name(self):
        self.assertTrue((await run_case(self.case('name-followup'), ROOT))['passed'])

    def test_six_runbook_fixtures_exist(self):
        cases = json.loads((ROOT / 'eval/cases.json').read_text())
        types = {c['expected_runbooks'][0] for c in cases if c['id'].startswith('type-')}
        self.assertEqual(types, {p.stem for p in (ROOT / 'data/runbooks').glob('*.md')})
        for case in cases:
            if case['id'].startswith('type-'):
                data = case['overrides']['auth-service']
                from agent.runbook_selector import select_runbooks
                self.assertEqual(select_runbooks([{'source': 'monitoring', 'data': data}], ['auth-service'])[0]['incident_types'],
                                 case['expected_runbooks'])

    async def test_requested_scope_only(self):
        case = self.case('payments-single')
        case['query'] = 'payments-service 장애 진단'
        result = await run_case(case, ROOT)
        self.assertTrue(result['checks']['route'])
        self.assertTrue(result['checks']['no_extra_targets'])

    async def test_both_required_manuals(self):
        result = await run_case(self.case('payments-cache'), ROOT)
        self.assertTrue(result['checks']['required_runbooks'])

    async def test_no_default_manual_for_healthy_system(self):
        result = await run_case(self.case('healthy-web'), ROOT)
        self.assertTrue(result['checks']['no_extra_runbooks'])

    async def test_short_third_draft_is_not_approved(self):
        result = await graph.validate_node({'draft': '오류', 'validation_attempts': 2, 'events': []}, None)
        self.assertNotIn('합격(', result['events'][-1]['verdict'])

    async def test_false_cpu_value_is_rejected(self):
        result = await graph.validate_node({
            'draft': '1) payments-service CPU는 1%로 정상입니다. 별도의 대응은 전혀 필요하지 않습니다.',
            'collected': [{'source': 'monitoring', 'data': {'system_name': 'payments-service', 'cpu_percent': 91.0}}],
            'events': []}, None)
        self.assertIsNotNone(result['validation_feedback'])

    async def test_missing_document_is_not_sufficient(self):
        result = await graph.replan_check_node({'events': [], 'collected': [
            {'source': 'runbook', 'data': '# Runbook Not Found'}]}, None)
        self.assertFalse(result['events'][-1]['is_sufficient'])

    async def test_partial_lookup_does_not_abort(self):
        result = await run_case(self.case('partial-failure'), ROOT)
        self.assertNotIn('error', result)

    async def test_unknown_service_not_investigated(self):
        case = self.case('unknown-billing')
        case['query'] = 'billing-service 장애 응답 지연 진단'
        result = await run_case(case, ROOT)
        self.assertEqual(result['targets'], [])


if __name__ == '__main__':
    unittest.main()
