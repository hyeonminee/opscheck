"""Real-time receipt is asserted while work is still blocked, not from final order."""
import asyncio
from contextlib import nullcontext
from functools import partial
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import AsyncMock, patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'eval')]
import httpx
import uvicorn
from agent.graph import build_graph
from agent.progress import end_steps
from agent.retry import with_retry
from harness import FixedMCP
import test_phase5 as phase5

class BlockedMCP(FixedMCP):
    def __init__(self):
        super().__init__(ROOT,{})
        self.slow_started=asyncio.Event();self.release=asyncio.Event();self.slow_finished=False
    async def call_tool(self,name,args):
        if args['system_name']=='cache-service':
            self.slow_started.set()
            await self.release.wait()
            self.slow_finished=True
        return await super().call_tool(name,args)

class ProgressTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=phase5.APITests.asyncSetUp
    asyncTearDown=phase5.APITests.asyncTearDown
    ask=phase5.APITests.ask

    async def test_custom_receipt_before_slow_lookup_and_writer_finish(self):
        mcp=BlockedMCP();self.deps.mcp=mcp
        graph=build_graph(self.deps)
        fast_received=asyncio.Event();running_received=asyncio.Event();write_received=asyncio.Event()
        write_started=asyncio.Event();write_release=asyncio.Event();original=self.deps.quality_llm.write
        async def write(*args,**kw):
            write_started.set();await write_release.wait();return await original(*args,**kw)
        self.deps.quality_llm.write=write
        events=[]
        async def consume():
            async for mode,payload in graph.astream({'query':'payments-service와 cache-service 진단','events':[],'history':[]},stream_mode=['updates','custom']):
                if mode=='custom' and payload.get('type')=='Progress':
                    events.append(payload['step']);s=payload['step']
                    if s['stage_id']=='monitoring:payments-service' and s['status']=='completed':fast_received.set()
                    if s['stage_id']=='monitoring:cache-service' and s['status']=='running':running_received.set()
                    if s['stage_id']=='write' and s['status']=='running':write_received.set()
        task=asyncio.create_task(consume())
        try:
            await asyncio.wait_for(asyncio.gather(mcp.slow_started.wait(),fast_received.wait(),running_received.wait()),2)
            self.assertFalse(mcp.slow_finished);self.assertFalse(task.done())
            mcp.release.set()
            await asyncio.wait_for(asyncio.gather(write_started.wait(),write_received.wait()),2)
            self.assertFalse(task.done())
        finally:
            mcp.release.set();write_release.set();await asyncio.wait_for(task,3)
        self.assertTrue(any(s['stage_id']=='validate' and s['status']=='rejected' for s in events))
        self.assertTrue(any(s['stage_id']=='write' and s['attempt']==2 and s['status']=='running' for s in events))

    async def test_retry_partial_failure_rewrite_and_saved_snapshot_have_stable_attempts(self):
        original=self.mcp.call_tool;counts={}
        async def unreliable(name,args):
            target=args['system_name'];counts[target]=counts.get(target,0)+1
            if target=='payments-service' and counts[target]==1:raise TimeoutError()
            if target=='cache-service':raise ValueError('controlled failure')
            return await original(name,args)
        self.mcp.call_tool=unreliable
        with patch('agent.graph.with_retry',side_effect=partial(with_retry,base_delay=0)):
            response=await self.ask('payments-service와 cache-service 진단')
        progress=response['progress'];byid={s['id']:s for s in progress}
        self.assertEqual(len(byid),len(progress))
        self.assertEqual(byid['monitoring:payments-service:attempt:1']['status'],'failed')
        self.assertEqual(byid['monitoring:payments-service:attempt:2']['status'],'completed')
        self.assertEqual(byid['monitoring:cache-service:attempt:1']['status'],'failed')
        self.assertEqual(byid['validate:attempt:1']['status'],'rejected')
        self.assertEqual(byid['validate:attempt:2']['status'],'completed')
        self.assertEqual(response['metadata']['processing_status'],'partial')
        self.assertEqual(response['metadata']['validation_status'],'passed')
        self.assertFalse(any(s['status'] in ('running','pending') for s in progress))
        record=self.store.get_investigation(response['metadata']['run_id'],'session')
        self.assertEqual(record['progress'],progress)
        self.assertEqual((await self.client.get('/investigations/'+record['run_id'],params={'thread_id':'session'})).json()['progress'],progress)

    async def test_chat_confirmation_conditional_skip_unverified_and_legacy(self):
        for q in ('안녕','로그인이 느려요','billing-service 느려요'):
            response=await self.ask(q)
            self.assertFalse(any(s['stage_id'] in ('plan','write','validate') or s['stage_id'].startswith('monitoring:') for s in response['progress']))
        self.deps.quality_llm.write=AsyncMock(return_value=('invalid',{'usd':0}))
        response=await self.ask('payments-service 진단')
        self.assertEqual(response['metadata']['validation_status'],'unverified')
        self.assertEqual(next(s for s in response['progress'] if s['stage_id']=='analysis')['status'],'skipped')
        self.assertEqual(next(s for s in response['progress'] if s['id']=='validate:attempt:2')['status'],'unverified')
        # Existing JSON records need no migration to remain readable.
        from test_phase6 import record
        old=record('legacy');self.store.append_exchange('session','q','a',investigation=old)
        detail=(await self.client.get('/investigations/legacy',params={'thread_id':'session'})).json()
        self.assertEqual(detail['reply'],old['reply']);self.assertNotIn('progress',detail)
        normalized=end_steps([{'id':'old','status':'running'},{'id':'next','status':'pending'}])
        self.assertEqual([s['status'] for s in normalized],['interrupted','skipped'])

    async def test_request_timeout_closes_active_steps_and_done_contract(self):
        async def blocked(*args,**kw):await asyncio.Event().wait()
        self.deps.quality_llm.write=blocked
        with patch('server.REQUEST_TIMEOUT',.1):
            response=await self.server.chat_stream('payments-service 진단','timeout')
            chunks=[c async for c in response.body_iterator]
        events=[json.loads(c[6:]) for c in chunks if c.startswith('data: {')]
        self.assertEqual(chunks[-1],'data: [DONE]\n\n')
        self.assertTrue(any(e['type']=='ProcessingError' for e in events))
        final=events[-1];self.assertEqual(final['type'],'FinalAnswer');self.assertEqual(final['validation_status'],'unverified')
        self.assertFalse(any(s['status'] in ('running','pending') for s in final['progress']))
        write=next(s for s in final['progress'] if s['stage_id']=='write')
        self.assertEqual(write['status'],'interrupted')
        record=self.store.get_investigation(final['run_id'],'timeout')
        self.assertEqual(record['progress'],final['progress'])

    async def test_live_tcp_proxy_receives_progress_before_release(self):
        # Two loopback HTTP servers, fixed MCP, fake models, temporary DB. No paid calls.
        import web_demo_server as proxy
        mcp=BlockedMCP();self.deps.mcp=mcp;self.server.app.state.graph=build_graph(self.deps)
        servers=[];tasks=[];sockets=[]
        def start(app):
            sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);sock.setblocking(False);sockets.append(sock)
            port=sock.getsockname()[1]
            server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,lifespan='off',log_level='error'))
            server.capture_signals=lambda:nullcontext()
            servers.append(server);tasks.append(asyncio.create_task(server.serve(sockets=[sock])))
            return port
        received=asyncio.Event();events=[]
        backend_port=start(self.server.app);proxy_port=start(proxy.app)
        original_backend=proxy.BACKEND;proxy.BACKEND=f'http://127.0.0.1:{backend_port}'
        async def consume():
            async with httpx.AsyncClient(timeout=5) as client:
                async with client.stream('GET',f'http://127.0.0.1:{proxy_port}/chat/stream',params={'message':'payments-service와 cache-service 진단','thread_id':'tcp'}) as response:
                    self.assertEqual(response.status_code,200)
                    async for line in response.aiter_lines():
                        if not line.startswith('data: '):continue
                        value=line[6:]
                        if value=='[DONE]':events.append('DONE');continue
                        e=json.loads(value);events.append(e)
                        if e.get('type')=='Progress' and e['step']['stage_id']=='monitoring:payments-service' and e['step']['status']=='completed':received.set()
        consumer=None
        try:
            async with asyncio.timeout(3):
                while not all(s.started for s in servers):await asyncio.sleep(.01)
            consumer=asyncio.create_task(consume())
            await asyncio.wait_for(asyncio.gather(received.wait(),mcp.slow_started.wait()),3)
            self.assertFalse(mcp.slow_finished);self.assertFalse(consumer.done())
            self.assertTrue(any(isinstance(e,dict) and e.get('type')=='Progress' and e['step']['stage_id']=='monitoring:cache-service' and e['step']['status']=='running' for e in events))
            mcp.release.set();await asyncio.wait_for(consumer,4)
            self.assertEqual(events[-1],'DONE');self.assertEqual(events[-2]['type'],'FinalAnswer')
            self.assertEqual(events[-2]['validation_status'],'passed')
            record=self.store.get_investigation(events[-2]['run_id'],'tcp')
            self.assertEqual(record['progress'],events[-2]['progress'])
        finally:
            mcp.release.set()
            if consumer and not consumer.done():consumer.cancel();await asyncio.gather(consumer,return_exceptions=True)
            for server in servers:server.should_exit=True
            await asyncio.gather(*tasks,return_exceptions=True)
            for sock in sockets:sock.close()
            proxy.BACKEND=original_backend

if __name__=='__main__':unittest.main()
