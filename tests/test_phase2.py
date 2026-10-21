"""Per-service manual selection with fixed MCP data. No paid model or user DB."""
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'eval'))
os.environ.update(LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph, replan_check_node
from harness import FixedMCP


class ManualsTests(unittest.IsolatedAsyncioTestCase):
    async def run_query(self, query, case=None):
        mcp = FixedMCP(ROOT, case or {})
        reads = []
        original = mcp.read_resource
        async def read(uri, server):
            reads.append(uri)
            return await original(uri, server)
        mcp.read_resource = read
        deps = SupervisorDeps(FastLLM('fixed', 0), QualityLLM('fixed', 0), mcp, None, 'v2')
        result = await build_graph(deps).ainvoke({'query': query, 'events': [], 'history': [], 'cost_usd': 0.0})
        return result, mcp, reads

    def books(self, result):
        return [c for c in result['collected'] if c['source'] == 'runbook']

    async def test_cpu_and_memory_keep_service_mapping(self):
        r, mcp, reads = await self.run_query('payments-service와 cache-service 장애 진단')
        books = {c.get('incident_type'): c for c in self.books(r)}
        self.assertEqual(set(books), {'cpu-spike', 'memory-leak'})
        self.assertEqual(books['cpu-spike']['related_systems'], ['payments-service'])
        self.assertEqual(books['memory-leak']['related_systems'], ['cache-service'])
        self.assertEqual(len(reads), 2)
        self.assertCountEqual(mcp.calls, ['payments-service', 'cache-service'])
        self.assertIn('메모리', r['final'])

    async def test_gateway_and_payments_do_not_lose_network_document(self):
        r, _, _ = await self.run_query('gateway-service와 payments-service 장애 진단')
        self.assertEqual({c.get('incident_type') for c in self.books(r)}, {'network-timeout', 'cpu-spike'})

    async def test_healthy_user_claim_does_not_create_db_or_cpu_manual(self):
        r, _, reads = await self.run_query('web-01 CPU 급증 장애 진단')
        self.assertEqual(reads, [])
        self.assertEqual(self.books(r), [])
        selection = next(c['data'] for c in r['collected'] if c['source'] == 'runbook_selection')
        self.assertEqual(selection[0]['status'], 'normal')

    async def test_unknown_observation_is_not_normal_or_db_down(self):
        unknown = {'system_name': 'auth-service', 'status': 'unknown', 'cpu_percent': None,
                   'memory_percent': None, 'alerts': [], 'last_checked': None}
        r, _, reads = await self.run_query('auth-service 장애 진단', {'overrides': {'auth-service': unknown}})
        self.assertEqual(reads, [])
        self.assertFalse([e for e in r['events'] if e['type'] == 'ReplanCheck'][-1]['is_sufficient'])
        self.assertTrue(next(c['data'] for c in r['collected'] if c['source'] == 'runbook_selection')[0]['limitations'])

    async def test_same_manual_is_fetched_once_for_two_services(self):
        snapshot = json.loads((ROOT / 'tests/fixtures/monitoring.json').read_text())['payments-service']
        snapshot = {**snapshot, 'system_name': 'cache-service'}
        r, mcp, reads = await self.run_query('payments-service와 cache-service 장애 진단', {'overrides': {'cache-service': snapshot}})
        self.assertEqual(len(reads), 1)
        doc = self.books(r)[0]
        self.assertCountEqual(doc['related_systems'], ['payments-service', 'cache-service'])
        self.assertEqual(doc['doc_id'], 'cpu-spike.md')
        self.assertEqual(doc['uri'], reads[0])
        self.assertEqual(set(doc['selection_reasons']), {'payments-service', 'cache-service'})
        self.assertEqual(len(mcp.calls), 2)
        self.assertEqual(r['replan_rounds'], 1)

    async def test_missing_manual_excluded_and_other_success_preserved(self):
        r, mcp, reads = await self.run_query('payments-service와 cache-service 장애 진단', {'missing_documents': ['memory-leak']})
        self.assertEqual({c.get('incident_type') for c in self.books(r)}, {'cpu-spike'})
        self.assertTrue([c for c in r['collected'] if c['source'] == 'runbook_failure'])
        check = [e for e in r['events'] if e['type'] == 'ReplanCheck'][-1]
        self.assertFalse(check['is_sufficient'])
        self.assertFalse(check['needs_more_research'])
        self.assertEqual(r['replan_rounds'], 1)
        self.assertEqual(len(reads), 2)
        self.assertEqual(len(mcp.calls), 2)
        self.assertFalse(any(e['type'] == 'TaskCompleted' and e.get('target') == 'memory-leak' for e in r['events']))

    async def test_only_missing_document_is_scheduled(self):
        snapshot = json.loads((ROOT / 'tests/fixtures/monitoring.json').read_text())
        collected = [{'source': 'monitoring', 'data': snapshot[n]} for n in ('payments-service', 'cache-service')]
        collected.append({'source': 'runbook', 'incident_type': 'cpu-spike', 'data': '# CPU manual', 'ok': True})
        r = await replan_check_node({'requested_targets': ['payments-service', 'cache-service'],
                                    'collected': collected, 'events': [], 'tasks': []}, None)
        self.assertEqual([t['target'] for t in r['tasks']], ['memory-leak'])

    async def test_round_cap_keeps_insufficient_verdict(self):
        snapshot = json.loads((ROOT / 'tests/fixtures/monitoring.json').read_text())['payments-service']
        r = await replan_check_node({'requested_targets': ['payments-service'], 'collected': [
            {'source': 'monitoring', 'data': snapshot}], 'events': [], 'tasks': [], 'replan_rounds': 1}, None)
        self.assertFalse(r['events'][-1]['is_sufficient'])
        self.assertFalse(r['events'][-1]['needs_more_research'])

    def test_six_types_positive_and_negative_with_numeric_limits(self):
        from agent.runbook_selector import select_runbooks
        normal = {'system_name': 'auth-service', 'status': 'healthy', 'cpu_percent': 20,
                  'memory_percent': 30, 'alerts': [], 'last_checked': '2026-10-21T00:00:00+00:00'}
        for kind, alerts, status in [('cpu-spike', ['CPU high'], 'degraded'),
                                    ('memory-leak', ['memory usage climbing'], 'degraded'),
                                    ('high-latency', ['connection pool 92% used'], 'degraded'),
                                    ('network-timeout', ['upstream timeout'], 'degraded'),
                                    ('disk-full', ['disk space low'], 'degraded'),
                                    ('db-down', ['database down'], 'down')]:
            with self.subTest(kind=kind):
                obs = {**normal, 'alerts': alerts, 'status': status}
                selected = select_runbooks([{'source': 'monitoring', 'data': obs}], ['auth-service'])[0]
                self.assertEqual(selected['incident_types'], [kind])
                selected = select_runbooks([{'source': 'monitoring', 'data': normal}], ['auth-service'])[0]
                self.assertNotIn(kind, selected['incident_types'])
        for field, value, kind in [('cpu_percent', 80, 'cpu-spike'), ('memory_percent', 90, 'memory-leak')]:
            obs = {**normal, field: value}
            self.assertIn(kind, select_runbooks([{'source': 'monitoring', 'data': obs}], ['auth-service'])[0]['incident_types'])
        for value in (None, '91', float('nan'), float('inf'), True, -2, 101):
            obs = {**normal, 'cpu_percent': value}
            self.assertNotIn('cpu-spike', select_runbooks([{'source': 'monitoring', 'data': obs}], ['auth-service'])[0]['incident_types'])

    def test_degraded_without_matching_evidence_is_insufficient(self):
        from agent.runbook_selector import select_runbooks
        for status in ('degraded', 'down', 'unknown'):
            obs = {'system_name': 'auth-service', 'status': status, 'cpu_percent': 20, 'memory_percent': 30, 'alerts': []}
            decision = select_runbooks([{'source': 'monitoring', 'data': obs}], ['auth-service'])[0]
            self.assertEqual(decision['status'], 'insufficient')
            self.assertEqual(decision['incident_types'], [])

class WriterMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_real_writer_adapters_receive_service_sources_and_limits(self):
        from agent.ollama_llm import OllamaQualityLLM
        from agent.openai_llm import OpenAIQualityLLM
        data = [{'source': 'runbook', 'data': '# CPU manual', 'doc_id': 'cpu-spike.md',
                 'uri': 'runbook://incident-response/cpu-spike', 'related_systems': ['payments-service'],
                 'selection_reasons': {'payments-service': ['CPU 91%']}},
                {'source': 'runbook_failure', 'data': 'missing memory-leak', 'related_systems': ['cache-service']}]
        for cls in (OllamaQualityLLM, OpenAIQualityLLM):
            model = cls('unit-test', 0)
            captured = []
            if cls is OllamaQualityLLM:
                async def fake(system, user, **kwargs):
                    captured.append(user)
                    return '1) test response', {'usd': 0}
                model._chat_stream = fake
            else:
                async def fake(system, user, **kwargs):
                    captured.append(user)
                    return '1) test response', {'usd': 0}
                model._chat = fake
            await model.write('진단', data, 1, None)
            self.assertIn('cpu-spike.md', captured[0])
            self.assertIn('runbook://incident-response/cpu-spike', captured[0])
            self.assertIn('payments-service', captured[0])
            self.assertIn('cache-service', captured[0])
            self.assertIn('missing memory-leak', captured[0])


if __name__ == '__main__':
    unittest.main()
