"""Phase 1: fixed MCP/model tests; no live model, ports, or user DB."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'eval'))
os.environ.update(LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph, gate_node
from agent.session_store import SqliteSessionStore
from agent.targets import requested_targets
from harness import FixedMCP


class Phase1Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'sessions.db'
        self.store = SqliteSessionStore(self.db)
        self.mcp = FixedMCP(ROOT, {})
        self.fast = FastLLM('fixed', 0)
        self.deps = SupervisorDeps(self.fast, QualityLLM('fixed', 0), self.mcp, self.store, 'v2')
        self.graph = build_graph(self.deps)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def ask(self, query, thread='one'):
        result = await self.graph.ainvoke({
            'query': query, 'thread_id': thread, 'history': self.store.load(thread),
            'session_context': self.store.load_context(thread), 'events': [], 'cost_usd': 0,
        })
        self.store.append_exchange(thread, query, result['final'], result.get('session_context'))
        return result

    async def test_single_and_missing_duplicate_extra_plan_targets(self):
        async def bad_plan(query, procedure):
            return {'needs_analysis': False, 'tasks': [
                {'agent': 'research', 'kind': 'monitoring', 'target': 'payments-service', 'status': 'pending'},
                {'agent': 'research', 'kind': 'monitoring', 'target': 'payments-service', 'status': 'pending'},
                {'agent': 'research', 'kind': 'monitoring', 'target': 'auth-service', 'status': 'pending'},
            ]}, {'usd': 0}
        self.fast.plan = bad_plan
        await self.ask('payments-service와 cache-service를 확인해줘')
        self.assertCountEqual(self.mcp.calls, ['payments-service', 'cache-service'])
        self.mcp.calls.clear()
        await self.ask('payments-service가 느려요', 'two')
        self.assertEqual(self.mcp.calls, ['payments-service'])

    async def test_unknown_and_mixed_requests_make_no_mcp_calls(self):
        for q in ['billing-service가 느려요', 'payments-service와 billing-service 확인해줘']:
            result = await self.ask(q)
            self.assertEqual(result['route'], 'clarify')
            self.assertIn('billing-service', result['final'])
        self.assertEqual(self.mcp.calls, [])

    async def test_unknown_correction_keeps_original_symptom(self):
        await self.ask('billing-service CPU가 급증했어요')
        result = await self.ask('payments-service요')
        self.assertEqual(self.mcp.calls, ['payments-service'])
        self.assertIn('CPU가 급증', result['query'])
        self.assertNotIn('billing-service', result['query'])

    async def test_clarification_and_followup_always_query_fresh_data(self):
        result = await self.ask('로그인이 느려요')
        self.assertEqual(result['route'], 'clarify')
        self.assertEqual(self.mcp.calls, [])
        result = await self.ask('auth-service요')
        self.assertIn('로그인이 느려요', result['query'])
        await self.ask('그 서비스 다시 확인해줘')
        self.assertEqual(self.mcp.calls, ['auth-service', 'auth-service'])
        self.assertNotIn('pending_query', self.store.load_context('one'))

    async def test_multi_target_followup_requires_confirmation(self):
        await self.ask('payments-service와 cache-service를 확인해줘')
        self.mcp.calls.clear()
        result = await self.ask('그 서비스 다시 확인해줘')
        self.assertEqual(result['route'], 'clarify')
        self.assertEqual(self.mcp.calls, [])
        await self.ask('cache-service요')
        self.assertEqual(self.mcp.calls, ['cache-service'])

    async def test_new_session_does_not_inherit_target(self):
        await self.ask('payments-service 확인해줘')
        self.mcp.calls.clear()
        result = await self.ask('그 서비스 다시 확인해줘', 'other')
        self.assertEqual(result['route'], 'clarify')
        self.assertEqual(self.mcp.calls, [])

    async def test_names_bypass_wrong_model_incident_route(self):
        self.fast.route = AsyncMock(return_value=({'route': 'incident'}, {'usd': 0}))
        for prefix in ['제', '내']:
            await self.ask(f'{prefix} 이름은 현민입니다', prefix)
            result = await self.ask('제 이름이 뭐였죠?', prefix)
            self.assertIn('현민님', result['final'])
        self.assertEqual(self.mcp.calls, [])

    async def test_name_memory_bypasses_llm_and_returns_no_fake_prompt(self):
        def no_llm(*args, **kwargs):
            raise AssertionError('explicit name memory must not call the LLM')
        self.fast.chat = no_llm
        self.fast.route = no_llm
        await self.ask('내 이름은 현민입니다')
        result = await self.ask('제 이름이 뭐였죠?')
        self.assertEqual(result['final'], '현민님이라고 말씀하셨어요.')
        self.assertIsNone(result.get('last_prompt'))
        self.assertEqual(result['cost_usd'], 0)
        self.assertTrue(any(e['type'] == 'ChatMemoryAnswered' for e in result['events']))
        other = await self.ask('제 이름이 뭐였죠?', 'new-session')
        self.assertNotIn('현민', other['final'])

    async def test_analysis_gate_ensures_exactly_one_or_zero(self):
        for required in [True, False]:
            for count in [0, 1, 3]:
                tasks = [{'agent': 'analysis', 'status': 'pending'}] * count
                result = await gate_node({'query': 'payments-service', 'tasks': tasks,
                                          'needs_analysis': required, 'events': []}, self.deps)
                self.assertEqual(sum(t['agent'] == 'analysis' for t in result['tasks']), int(required))

    async def test_analysis_execution_matches_plan_and_ignores_route_flag(self):
        for needed in [True, False]:
            original = self.fast.plan
            self.fast.route = AsyncMock(return_value=({'route': 'incident', 'needs_analysis': not needed}, {'usd': 0}))
            self.fast.plan = AsyncMock(return_value=({'needs_analysis': needed, 'tasks': []}, {'usd': 0}))
            result = await self.ask('CPU 장애를 payments-service에서 조사해줘')
            self.assertEqual(sum(e['type'] == 'AnalysisCompleted' for e in result['events']), int(needed))
            self.fast.plan = original

    async def test_restart_restores_messages_and_pending_context(self):
        await self.ask('로그인이 느려요')
        self.store.close()
        self.store = SqliteSessionStore(self.db)
        self.deps.session_store = self.store
        self.assertEqual(len(self.store.load('one')), 2)
        result = await self.ask('auth-service요')
        self.assertIn('로그인이 느려요', result['query'])
        self.assertEqual(self.mcp.calls, ['auth-service'])

    async def test_api_post_sse_restore_and_legacy_storage(self):
        import httpx
        import server
        server.app.state.deps = self.deps
        server.app.state.graph = self.graph
        server.app.state.llm_backend = 'fake'
        server.app.state.instance_id = 1
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://test') as client:
            first = await client.get('/chat/stream', params={'message': '로그인이 느려요', 'thread_id': 'api'})
            self.assertIn('[DONE]', first.text)
            self.assertIn('TargetConfirmationRequired', first.text)
            second = await client.post('/chat', json={'message': 'auth-service요', 'thread_id': 'api'})
            self.assertEqual(second.status_code, 200)
            self.assertEqual(self.mcp.calls, ['auth-service'])
            records = (await client.get('/sessions/api/messages')).json()['messages']
            self.assertEqual([r['role'] for r in records], ['user', 'assistant'] * 2)
            self.assertEqual((await client.get('/sessions/new/messages')).json()['messages'], [])
            self.deps.version = 'v1'
            response = await client.get('/chat/stream', params={'message': '안녕', 'thread_id': 'legacy'})
            self.assertIn('[DONE]', response.text)
            self.assertEqual(len(self.store.load('legacy')), 2)
            await client.post('/chat', json={'message': '안녕', 'thread_id': 'legacy'})
            self.assertEqual(len(self.store.load('legacy')), 4)

    def test_registry_boundaries_no_partial_match_or_fallback(self):
        self.assertEqual(requested_targets('PAYMENTS-SERVICE가 느려요'), (['payments-service'], []))
        self.assertEqual(requested_targets('payments-service-3 장애'), ([], ['payments-service-3']))
        self.assertEqual(requested_targets('web-02 확인'), ([], ['web-02']))
        self.assertEqual(requested_targets('로그인'), ([], []))

    async def test_model_adapters_never_substitute_unknown_or_missing_targets(self):
        for module_name, class_name in [('agent.ollama_llm', 'OllamaFastLLM'), ('agent.openai_llm', 'OpenAIFastLLM')]:
            module = importlib.import_module(module_name)
            model = getattr(module, class_name)('unused', 0)
            model._chat = AsyncMock(return_value=(json.dumps({'tasks': [{'agent': 'research', 'target': 'auth-service'}], 'needs_analysis': False}), {'usd': 0}))
            result, _ = await model.plan('billing-service가 느려요', 'procedure')
            self.assertEqual(result['tasks'], [])
            result, _ = await model.plan('payments-service와 cache-service 확인', 'procedure')
            self.assertCountEqual([t['target'] for t in result['tasks']], ['payments-service', 'cache-service'])
            model._chat = AsyncMock(return_value=('{"route":"chat","needs_analysis":true}', {'usd': 0}))
            result, _ = await model.route('안녕')
            self.assertEqual(result, {'route': 'chat'})

    def test_startup_scripts_preserve_database(self):
        for name in ['run_agent_ollma.sh', 'run_agent_openai.sh']:
            self.assertNotIn('rm -f session.db', (ROOT / 'sh' / name).read_text())


if __name__ == '__main__':
    unittest.main()
