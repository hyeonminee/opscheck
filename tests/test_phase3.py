"""Failure injection, deadlines and HTTP contracts without live API calls/user DB."""
import asyncio
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'eval'))
os.environ.update(LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false',LLM_BACKEND='fake')
import httpx
from agent.graph import build_graph, research_node
from agent.fake_llm import FastLLM,QualityLLM
from agent.deps import SupervisorDeps
from agent.session_store import SqliteSessionStore
from agent.retry import with_retry
from harness import FixedMCP


class FailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mcp=FixedMCP(ROOT,{'fail_targets':['cache-service']})
        self.fast=FastLLM('fixed',0);self.quality=QualityLLM('fixed',0)
        self.deps=SupervisorDeps(self.fast,self.quality,self.mcp,None,'v2')

    async def ask(self,query):
        return await build_graph(self.deps).ainvoke({'query':query,'history':[],'events':[],'cost_usd':0.0})

    async def test_partial_failure_keeps_observations_and_failed_target(self):
        r=await self.ask('payments-service와 cache-service 장애 진단')
        self.assertTrue(any(c['source']=='monitoring' and c['data']['system_name']=='payments-service' for c in r['collected']))
        self.assertTrue(any(c['source']=='monitoring_failure' and c['target']=='cache-service' for c in r['collected']))
        self.assertIn('cache-service',r['final']);self.assertIn('미확인',r['final'])
        self.assertEqual(r['processing_status'],'partial')

    async def test_all_failures_skip_writer_and_analysis(self):
        self.mcp.case['fail_targets']=['payments-service','cache-service']
        self.quality.write=AsyncMock(side_effect=AssertionError('writer must not run'))
        self.fast.analyze=AsyncMock(side_effect=AssertionError('analysis must not run'))
        r=await self.ask('payments-service와 cache-service 공통 장애 진단')
        self.assertIn('진단할 수 없습니다',r['final']);self.assertNotIn('0%',r['final'])
        self.assertNotIn('형식 검사',r['final']);self.assertIn('모든 대상',r['final'])
        self.assertEqual(r['processing_status'],'failed');self.quality.write.assert_not_awaited()

    async def test_timeouts_retry_twice_and_permanent_failure_once(self):
        transient=AsyncMock(side_effect=[TimeoutError(),{'ok':True}])
        result,count=await with_retry(transient,base_delay=0)
        self.assertTrue(result['ok']);self.assertEqual(count,1)
        transient=AsyncMock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):await with_retry(transient,base_delay=0)
        self.assertEqual(transient.await_count,2)
        permanent=AsyncMock(side_effect=ValueError('invalid input'))
        with self.assertRaises(ValueError):await with_retry(permanent,base_delay=0)
        self.assertEqual(permanent.await_count,1)

    async def test_per_attempt_deadline_cancels_and_recovers(self):
        canceled=[]
        async def slow():
            try:await asyncio.sleep(1)
            finally:canceled.append(True)
        with self.assertRaises(TimeoutError):await with_retry(slow,attempt_timeout=.01,base_delay=0)
        self.assertEqual(len(canceled),2)
        good=AsyncMock(return_value=7)
        self.assertEqual(await with_retry(good,attempt_timeout=.01),(7,0))

    async def test_invalid_plan_ends_before_any_tool(self):
        state={'tasks':[{'agent':'research','kind':'monitoring','target':'billing-service','status':'pending'}],'events':[]}
        with self.assertRaises(ValueError):await research_node(state,self.deps)
        self.assertEqual(self.mcp.calls,[])


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import server
        self.server=server;self.temp=tempfile.TemporaryDirectory();self.store=SqliteSessionStore(Path(self.temp.name)/'sessions.db')
        self.mcp=FixedMCP(ROOT,{})
        self.deps=SupervisorDeps(FastLLM('fixed',0),QualityLLM('fixed',0),self.mcp,self.store,'v2')
        server.app.state.deps=self.deps;server.app.state.graph=build_graph(self.deps)
        server.app.state.llm_backend='fake';server.app.state.instance_id=1
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose();self.store.close();self.temp.cleanup()

    async def test_whole_request_timeout_and_health_remains_responsive(self):
        entered=asyncio.Event();canceled=asyncio.Event()
        async def slow(*args):
            entered.set()
            try:await asyncio.sleep(1)
            finally:canceled.set()
        self.deps.fast_llm.plan=slow
        with patch.object(self.server,'REQUEST_TIMEOUT',.08):
            task=asyncio.create_task(self.client.post('/chat',json={'message':'payments-service 진단','thread_id':'timeout'}))
            await asyncio.wait_for(entered.wait(),.5)
            health=await asyncio.wait_for(self.client.get('/health'),timeout=.03)
            self.assertEqual(health.status_code,200)
            r=await task
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['events'][-1]['code'],'timeout')
        self.assertTrue(canceled.is_set());self.assertIn('시간',r.json()['reply'])
        self.deps.fast_llm.plan=FastLLM('fresh',0).plan
        r=await self.client.post('/chat',json={'message':'제 이름은 민수입니다','thread_id':'fresh'})
        self.assertIn('민수',r.json()['reply'])

    async def test_sse_exception_has_safe_error_final_and_done(self):
        self.deps.fast_llm.plan=AsyncMock(side_effect=RuntimeError('SECRET_SENTINEL'))
        r=await self.client.get('/chat/stream',params={'message':'payments-service 진단','thread_id':'bad'})
        self.assertIn('ProcessingError',r.text);self.assertIn('FinalAnswer',r.text);self.assertIn('[DONE]',r.text)
        self.assertNotIn('SECRET_SENTINEL',r.text)
        self.assertEqual(len(self.store.load('bad')),2)

    async def test_canceled_request_does_not_save_completed_reply(self):
        started=asyncio.Event()
        async def slow(*args):started.set();await asyncio.sleep(10)
        self.deps.fast_llm.plan=slow
        task=asyncio.create_task(self.client.post('/chat',json={'message':'payments-service 진단','thread_id':'canceled'}))
        await asyncio.wait_for(started.wait(),.5);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.store.load('canceled'),[])

    async def test_generation_limit_replaces_draft_with_verified_observations(self):
        from agent.execution import GenerationLimitError
        self.deps.quality_llm.write=AsyncMock(side_effect=GenerationLimitError())
        r=await self.client.get('/chat/stream',params={'message':'payments-service 진단','thread_id':'truncated'})
        self.assertIn('generation_limit',r.text);self.assertIn('91.0',r.text);self.assertIn('[DONE]',r.text)

    async def test_writer_timeout_preserves_already_collected_observations(self):
        async def slow(*args,**kwargs):await asyncio.sleep(1)
        self.deps.quality_llm.write=slow
        with patch.object(self.server,'REQUEST_TIMEOUT',.08):
            r=await self.client.post('/chat',json={'message':'payments-service 진단','thread_id':'writer-timeout'})
        self.assertEqual(r.json()['metadata']['processing_status'],'failed')
        self.assertIn('91.0',r.json()['reply']);self.assertIn('timeout',r.text)

    async def test_two_rejected_drafts_end_without_forced_approval(self):
        self.deps.quality_llm.write=AsyncMock(return_value=('짧음',{'usd':0}))
        r=await self.client.post('/chat',json={'message':'payments-service 진단','thread_id':'limit'})
        self.assertEqual(self.deps.quality_llm.write.await_count,2)
        self.assertEqual(r.json()['metadata']['validation_status'],'unverified')
        self.assertIn('91.0',r.json()['reply']);self.assertNotEqual(r.json()['reply'],'짧음')


class AdapterLimitTests(unittest.IsolatedAsyncioTestCase):
    def test_every_model_contract_is_async(self):
        from agent.ollama_llm import OllamaFastLLM
        from agent.openai_llm import OpenAIFastLLM
        for cls in (FastLLM,OllamaFastLLM,OpenAIFastLLM):
            for method in ('route','plan','analyze'):self.assertTrue(inspect.iscoroutinefunction(getattr(cls,method)))

    async def test_openai_async_cap_truncation_and_http_cleanup(self):
        from agent.openai_llm import OpenAIQualityLLM
        from agent.execution import GenerationLimitError,ANSWER_TOKENS
        captured=[]
        async def respond(request):
            captured.append(json.loads(request.content));await asyncio.sleep(.01)
            return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'partial'}}],'usage':{'total_tokens':12}})
        original=httpx.AsyncClient;model=OpenAIQualityLLM('unit-test',0);model.api_key='unit-test-placeholder';clients=[]
        def client(**kw):
            value=original(transport=httpx.MockTransport(respond),**kw);clients.append(value);return value
        with patch('agent.openai_llm.httpx.AsyncClient',side_effect=client):
            with self.assertRaises(GenerationLimitError):await model.write('진단',[],1,None)
        self.assertEqual(captured[0]['max_tokens'],ANSWER_TOKENS);self.assertTrue(clients[0].is_closed)

    async def test_ollama_stream_requires_done_and_rejects_length(self):
        from agent.ollama_llm import OllamaQualityLLM
        from agent.execution import GenerationLimitError,ANSWER_TOKENS
        for ending in ('length','missing-done'):
            captured=[]
            def respond(request):
                captured.append(json.loads(request.content))
                data={'message':{'content':'partial'},'done':ending!='missing-done','done_reason':'length'}
                return httpx.Response(200,text=json.dumps(data)+'\n')
            original=httpx.AsyncClient
            with patch('agent.ollama_llm.httpx.AsyncClient',side_effect=lambda **kw: original(transport=httpx.MockTransport(respond),**kw)):
                with self.assertRaises(GenerationLimitError):await OllamaQualityLLM('unit-test',0).write('진단',[],1,None)
            self.assertEqual(captured[0]['options']['num_predict'],ANSWER_TOKENS)
            self.assertFalse(captured[0]['think'])

    async def test_model_total_deadline_closes_http_and_allows_other_work(self):
        from agent.openai_llm import OpenAIFastLLM
        async def respond(request):await asyncio.sleep(1)
        original=httpx.AsyncClient;clients=[]
        def client(**kw):
            value=original(transport=httpx.MockTransport(respond),**kw);clients.append(value);return value
        model=OpenAIFastLLM('unit-test',0);model.api_key='unit-test-placeholder'
        with patch('agent.openai_llm.httpx.AsyncClient',side_effect=client),patch('agent.openai_llm.MODEL_TIMEOUT',.02):
            task=asyncio.create_task(model.route('안녕'))
            await asyncio.wait_for(asyncio.sleep(.001),.01)
            with self.assertRaises(TimeoutError):await task
        self.assertTrue(clients[0].is_closed)

    async def test_both_writers_receive_failed_monitoring_target(self):
        from agent.openai_llm import OpenAIQualityLLM
        from agent.ollama_llm import OllamaQualityLLM
        for cls in (OpenAIQualityLLM,OllamaQualityLLM):
            model=cls('unit-test',0);captured=[]
            async def respond(system,user,**kwargs):
                captured.append(user);return '1) test',{'usd':0}
            if cls is OpenAIQualityLLM:model._chat=respond
            else:model._chat_stream=respond
            await model.write('two services',[{'source':'monitoring_failure','target':'cache-service','data':'조회 실패'}],1,None)
            self.assertIn('cache-service',captured[0]);self.assertIn('조회 실패',captured[0])


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_http_error_and_complete_streams(self):
        import web_demo_server as proxy
        original=httpx.AsyncClient
        for mode in ('incomplete','bad-http','disconnected','complete'):
            def respond(request):
                if mode=='disconnected':raise httpx.ConnectError('SECRET_SENTINEL')
                if mode=='bad-http':return httpx.Response(503,text='SECRET_SENTINEL')
                return httpx.Response(200,text='data: {"type":"AnswerToken","delta":"draft"}\n\n'+('data: [DONE]\n\n' if mode=='complete' else ''))
            async with original(transport=httpx.ASGITransport(app=proxy.app),base_url='http://proxy') as caller:
                with patch('web_demo_server.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(respond),**kw)):
                    response=await caller.get('/chat/stream',params={'message':'test'})
            self.assertEqual(response.text.count('data: [DONE]'),1)
            self.assertEqual('ProcessingError' in response.text,mode!='complete')
            self.assertNotIn('SECRET_SENTINEL',response.text)


class MCPReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_timeout_then_reuse_same_connection(self):
        from agent.mcp_client import MCPClient
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'slow_mcp.py'
            script.write_text("from fastmcp import FastMCP\nimport asyncio\nmcp=FastMCP('deadline-test')\n@mcp.tool()\nasync def get_monitoring_status(system_name:str)->dict:\n    if system_name=='slow':await asyncio.sleep(2)\n    return {'system_name':system_name}\nif __name__=='__main__':mcp.run(transport='stdio',show_banner=False)\n")
            client=MCPClient(flaky_first_tool_call=False,server_scripts={'ops':script})
            await client.start()
            try:
                with self.assertRaises(TimeoutError):
                    await with_retry(client.call_tool,'get_monitoring_status',{'system_name':'slow'},attempt_timeout=.03,base_delay=0)
                result=await asyncio.wait_for(client.call_tool('get_monitoring_status',{'system_name':'fast'}),1)
                self.assertEqual(result,{'system_name':'fast'})
            finally:await client.stop()


if __name__=='__main__':unittest.main()
