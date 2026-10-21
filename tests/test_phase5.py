"""Investigation persistence and API contracts without user DB/live model."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
from uuid import UUID

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'eval')]
os.environ.update(LLM_BACKEND='fake',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
import httpx
from agent.session_store import SqliteSessionStore
from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM,QualityLLM
from agent.graph import build_graph
from harness import FixedMCP


class StoreTests(unittest.TestCase):
    def record(self,run='one',thread='session'):
        return {'run_id':run,'thread_id':thread,'created':'2026-10-21T00:00:00+00:00','query':'진단',
                'targets':['payments-service'],'processing_status':'partial','validation_status':'unverified',
                'is_sample':True,'report':{'actions':[]},'collected':[{'source':'monitoring','data':{'cpu_percent':91.0}}]}

    def test_existing_database_migration_preserves_messages_context_and_restart_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db.sqlite3'
            with sqlite3.connect(path) as old:
                old.execute('CREATE TABLE messages(thread_id TEXT,role TEXT,content TEXT,created TEXT)')
                old.execute('INSERT INTO messages VALUES (?,?,?,?)',('session','user','기존 메시지','old'))
                old.execute('CREATE TABLE session_context(thread_id TEXT PRIMARY KEY,context TEXT NOT NULL)')
                old.execute('INSERT INTO session_context VALUES (?,?)',('session','{"last_targets":["auth-service"]}'))
            old.close()
            store=SqliteSessionStore(path)
            self.assertEqual(store.load('session')[0]['content'],'기존 메시지')
            self.assertEqual(store.load_context('session')['last_targets'],['auth-service'])
            record=self.record();store.append_exchange('session','진단','미통과',investigation=record)
            store.close();store=SqliteSessionStore(path)
            self.assertEqual(store.get_investigation('one','session'),record)
            self.assertEqual(len(store.load('session')),3);store.close()

    def test_duplicate_completion_does_not_duplicate_messages_or_record(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SqliteSessionStore(Path(directory)/'db')
            for _ in range(2):store.append_exchange('session','질문','답변',investigation=self.record())
            self.assertEqual(len(store.list_investigations('session')),1)
            self.assertEqual(len(store.load('session')),2)
            with self.assertRaises(ValueError):store.append_exchange('other','질문','답변',investigation=self.record('one','other'))
            self.assertEqual(store.load('other'),[]);store.close()

    def test_newest_first_limit_and_session_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SqliteSessionStore(Path(directory)/'db')
            for i in range(3):store.append_exchange('session','q','a',investigation=self.record(str(i)))
            store.append_exchange('other','q','a',investigation=self.record('private','other'))
            self.assertEqual([r['run_id'] for r in store.list_investigations('session',2)],['2','1'])
            self.assertIsNone(store.get_investigation('private','session'))
            self.assertNotIn('collected',store.list_investigations('session')[0]);store.close()

    def test_serialization_failure_leaves_no_partial_exchange_or_investigation(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SqliteSessionStore(Path(directory)/'db');record=self.record()
            with self.assertRaises(TypeError):store.append_exchange('session','q','a',context={'bad':object()},investigation=record)
            self.assertEqual(store.load('session'),[]);self.assertEqual(store.list_investigations('session'),[]);store.close()


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import server
        self.server=server;self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'db.sqlite3'
        self.store=SqliteSessionStore(self.path)
        self.mcp=FixedMCP(ROOT,{})
        self.deps=SupervisorDeps(FastLLM('fixed',0),QualityLLM('fixed',0),self.mcp,self.store,'v2')
        server.app.state.deps=self.deps;server.app.state.graph=build_graph(self.deps)
        server.app.state.llm_backend='fake';server.app.state.instance_id=1
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose();self.deps.session_store.close();self.temp.cleanup()

    async def ask(self,query='payments-service 진단',thread='session'):
        response=await self.client.post('/chat',json={'message':query,'thread_id':thread});self.assertEqual(response.status_code,200)
        return response.json()

    async def test_post_unique_id_snapshot_and_scoped_list_detail(self):
        response=await self.ask();run=response['metadata']['run_id'];UUID(run)
        record=(await self.client.get('/investigations/'+run,params={'thread_id':'session'})).json()
        self.assertEqual(record['report'],response['report']);self.assertEqual(record['reply'],response['reply'])
        self.assertEqual(record['validation_status'],'passed');self.assertTrue(record['is_sample'])
        self.assertTrue(any(c.get('source')=='monitoring' and c['data']['cpu_percent']==91.0 for c in record['collected']))
        self.assertNotIn('draft',record);self.assertNotIn('last_prompt',record)
        listed=(await self.client.get('/investigations',params={'thread_id':'session'})).json()['investigations']
        self.assertEqual(len(listed),1);self.assertEqual(listed[0]['run_id'],run)
        self.assertEqual((await self.client.get('/investigations/'+run,params={'thread_id':'other'})).status_code,404)
        self.assertEqual((await self.client.get('/investigations')).status_code,422)
        self.assertEqual((await self.client.get('/investigations',params={'thread_id':'session','limit':101})).status_code,422)

    async def test_sse_stores_once_before_final_and_separate_http_requests_are_distinct(self):
        async def collect():
            response=await self.client.get('/chat/stream',params={'message':'payments-service 진단','thread_id':'session'})
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {')]
            return next(e for e in events if e['type']=='FinalAnswer')
        first=await collect();second=await collect();self.assertNotEqual(first['run_id'],second['run_id'])
        UUID(first['run_id']);self.assertEqual(len(self.store.list_investigations('session')),2)
        self.assertEqual(self.store.get_investigation(first['run_id'],'session')['report'],first['report'])
        self.assertEqual(len(self.store.load('session')),4)

    async def test_sse_record_exists_before_final_event_and_midstream_cancel_is_not_saved(self):
        response=await self.server.chat_stream('payments-service 진단','before-final')
        async for chunk in response.body_iterator:
            if '"type": "FinalAnswer"' in chunk:
                event=json.loads(chunk.removeprefix('data: ').strip())
                self.assertIsNotNone(self.store.get_investigation(event['run_id'],'before-final'))
                self.assertEqual(len(self.store.load('before-final')),2)
        started=asyncio.Event()
        async def slow(*args,**kwargs):started.set();await asyncio.sleep(5)
        self.deps.quality_llm.write=slow
        response=await self.server.chat_stream('payments-service 진단','canceled-stream')
        async def consume():
            async for chunk in response.body_iterator:pass
        task=asyncio.create_task(consume());await asyncio.wait_for(started.wait(),.5);task.cancel()
        with self.assertLogs(level='WARNING') as captured:
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertIn('91.0',str(captured.output))
        self.assertEqual(self.store.list_investigations('canceled-stream'),[])
        self.assertEqual(self.store.load('canceled-stream'),[])

    async def test_chat_and_target_confirmation_do_not_create_investigations(self):
        for query in ('안녕','로그인이 느려요','billing-service 느려요'):
            response=await self.ask(query);UUID(response['metadata']['run_id'])
        self.assertEqual(self.store.list_investigations('session'),[])
        self.assertEqual(len(self.store.load('session')),6)

    async def test_partial_and_unverified_records_retain_final_state_only(self):
        self.mcp.case['fail_targets']=['cache-service']
        response=await self.ask('payments-service와 cache-service 진단')
        partial=self.store.get_investigation(response['metadata']['run_id'],'session')
        self.assertEqual(partial['processing_status'],'partial');self.assertEqual(partial['validation_status'],'passed')
        self.assertTrue(any(c['source']=='monitoring_failure' for c in partial['collected']))
        self.deps.quality_llm.write=AsyncMock(return_value=('REJECTED_ACTION_SENTINEL',{'usd':0}))
        response=await self.ask();record=self.store.get_investigation(response['metadata']['run_id'],'session')
        self.assertEqual(record['validation_status'],'unverified');self.assertEqual(record['report']['actions'],[])
        self.assertNotIn('REJECTED_ACTION_SENTINEL',json.dumps(record))

    async def test_finished_timeout_is_recorded_but_cancellation_is_not(self):
        started=asyncio.Event()
        async def slow(*args):started.set();await asyncio.sleep(5)
        self.deps.fast_llm.plan=slow
        with patch.object(self.server,'REQUEST_TIMEOUT',.03):
            response=await self.ask(thread='timeout')
        record=self.store.get_investigation(response['metadata']['run_id'],'timeout')
        self.assertEqual(record['processing_status'],'failed');self.assertEqual(record['validation_status'],'unverified')
        started.clear();task=asyncio.create_task(self.ask(thread='canceled'))
        await asyncio.wait_for(started.wait(),.5);task.cancel()
        with self.assertLogs(level='WARNING') as captured:
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertIn('sample_observations',str(captured.output))
        self.assertEqual(self.store.list_investigations('canceled'),[]);self.assertEqual(self.store.load('canceled'),[])

    async def test_restart_restores_report_and_previous_messages(self):
        response=await self.ask();self.store.close()
        self.deps.session_store=SqliteSessionStore(self.path)
        record=(await self.client.get('/investigations/'+response['metadata']['run_id'],params={'thread_id':'session'})).json()
        self.assertEqual(record['report'],response['report']);self.assertEqual(len(self.deps.session_store.load('session')),2)

    async def test_proxy_list_detail_encoding_and_upstream_error(self):
        import web_demo_server as proxy
        original=httpx.AsyncClient;seen=[]
        def respond(request):
            seen.append(request.url)
            if request.url.path=='/investigations':return httpx.Response(200,json={'investigations':[]})
            return httpx.Response(404,json={'detail':'not in this session'})
        async with original(transport=httpx.ASGITransport(app=proxy.app),base_url='http://proxy') as client:
            with patch('web_demo_server.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(respond),**kw)):
                response=await client.get('/investigations',params={'thread_id':'session & x','limit':3})
                self.assertEqual(response.json(),{'investigations':[]});self.assertEqual(seen[-1].params['thread_id'],'session & x')
                self.assertEqual((await client.get('/investigations/id',params={'thread_id':'session'})).status_code,404)
            with patch('web_demo_server.httpx.AsyncClient',side_effect=httpx.ConnectError('private sentinel')):
                response=await client.get('/investigations',params={'thread_id':'session'})
                self.assertEqual(response.status_code,502);self.assertNotIn('sentinel',response.text)


if __name__=='__main__':unittest.main()
