"""SSE request trace lifetime and saved IDs. Local SDK tracing only; no upload."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'eval')]
from langsmith import Client, tracing_context
from langsmith.run_helpers import get_current_run_tree
from langsmith.run_trees import RunTree
from agent.mcp_client import MCPClient
from agent.graph import build_graph
import test_phase5 as phase5

class SSETracingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=phase5.APITests.asyncSetUp
    asyncTearDown=phase5.APITests.asyncTearDown

    @contextmanager
    def tracing(self, enabled='local'):
        # Local SDK mode + explicit inert client + transport guard prevent export.
        client=Client(api_url='http://127.0.0.1:1',api_key='offline-test',auto_batch_tracing=False,info={'version':'offline-test'})
        self.addCleanup(client.close)
        ended={};original=RunTree.end
        def capture(run,*args,**kwargs):
            original(run,*args,**kwargs);ended[str(run.id)]=run
        with patch.object(RunTree,'end',capture), patch.object(Client,'create_run') as creates, patch.object(Client,'update_run') as updates, patch('requests.sessions.Session.request',side_effect=AssertionError('External tracing HTTP disabled')) as network, tracing_context(enabled=enabled,client=client,project_name='offline-sse-test'):
            self.trace_creates=creates;self.trace_updates=updates
            yield ended
            self.assertEqual(network.call_count,0)

    async def stream(self,thread='sse'):
        response=await self.server.chat_stream('payments-service 진단',thread)
        chunks=[chunk async for chunk in response.body_iterator]
        self.assertEqual(chunks[-1],'data: [DONE]\n\n')
        events=[json.loads(c[6:]) for c in chunks if c.startswith('data: {')]
        self.assertEqual(events[-1]['type'],'FinalAnswer')
        return events

    async def test_post_and_sse_save_same_request_root_contract_with_real_mcp_children(self):
        mcp=MCPClient(flaky_first_tool_call=False)
        await mcp.start()
        self.deps.mcp=mcp;self.server.app.state.graph=build_graph(self.deps)
        try:
            with self.tracing() as runs:
                post=await self.client.post('/chat',json={'message':'payments-service 진단','thread_id':'post'})
                post_id=post.json()['metadata']['langsmith_run_id']
                self.assertTrue(post_id)
                self.assertEqual(runs[post_id].name,'chat_request')
                events=await self.stream()
                final=events[-1];trace_id=final.get('langsmith_run_id')
                self.assertTrue(trace_id)
                self.assertNotEqual(trace_id,post_id)
                record=self.store.get_investigation(final['run_id'],'sse')
                self.assertEqual(record['langsmith_run_id'],trace_id)
                root=runs[trace_id]
                self.assertEqual(root.name,'chat_request');self.assertIsNone(root.parent_run_id)
                self.assertIsNotNone(root.end_time);self.assertFalse(root.error)
                self.assertEqual(root.outputs['run_id'],final['run_id'])
                self.assertEqual(root.outputs['validation_status'],'passed')
                children=[r for r in runs.values() if str(r.trace_id)==trace_id and str(r.id)!=trace_id]
                names={r.name for r in children}
                for name in ('route_node','plan_node','research_node','write_node','validate_node','mcp.call_tool','mcp.read_resource','mcp.get_prompt'):
                    self.assertIn(name,names)
                self.assertTrue(any(r.run_type=='llm' for r in children))
                self.assertIsNone(get_current_run_tree())
        finally:await mcp.stop()

    async def test_processing_error_retains_trace_link_and_safe_termination(self):
        self.deps.fast_llm.plan=AsyncMock(side_effect=RuntimeError('PRIVATE_ERROR_SENTINEL'))
        with self.tracing() as runs:
            with self.assertLogs(level='WARNING'):
                events=await self.stream('failure')
            final=events[-1];trace_id=final.get('langsmith_run_id')
            self.assertTrue(trace_id)
            self.assertEqual(self.store.get_investigation(final['run_id'],'failure')['langsmith_run_id'],trace_id)
            self.assertEqual(final['processing_status'],'failed')
            self.assertTrue(any(e['type']=='ProcessingError' for e in events))
            self.assertEqual(runs[trace_id].outputs['processing_status'],'failed')
            self.assertEqual(runs[trace_id].error,'processing_error')
            self.assertNotIn('PRIVATE_ERROR_SENTINEL',json.dumps(events))
            self.assertIsNone(get_current_run_tree())

    async def test_timeout_retains_trace_and_cancellation_does_not_save_history(self):
        entered=asyncio.Event()
        async def block(*args,**kwargs):entered.set();await asyncio.Event().wait()
        self.deps.quality_llm.write=block
        with self.tracing() as runs:
            with patch('server.REQUEST_TIMEOUT',.08),self.assertLogs(level='WARNING'):
                events=await self.stream('timeout')
            final=events[-1];trace_id=final.get('langsmith_run_id')
            self.assertTrue(trace_id);self.assertEqual(runs[trace_id].error,'timeout')
            entered.clear()
            response=await self.server.chat_stream('payments-service 진단','cancel')
            async def consume():
                async for chunk in response.body_iterator:pass
            task=asyncio.create_task(consume())
            await asyncio.wait_for(entered.wait(),2)
            with self.assertLogs(level='WARNING'):
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
            canceled=[r for r in runs.values() if r.name=='chat_request' and r.inputs.get('thread_id')=='cancel']
            self.assertEqual(len(canceled),1)
            self.assertTrue(canceled[0].error);self.assertIsNotNone(canceled[0].end_time)
            self.assertEqual(self.store.load('cancel'),[])
            self.assertEqual(self.store.list_investigations('cancel'),[])
            self.assertIsNone(get_current_run_tree())

    async def test_enabled_sdk_sends_matching_request_ids_to_inert_client(self):
        with self.tracing(enabled=True) as runs:
            response=await self.client.get('/chat/stream',params={'message':'payments-service 진단','thread_id':'enabled'})
            self.assertTrue(response.text.endswith('data: [DONE]\n\n'))
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {')]
            trace_id=events[-1]['langsmith_run_id']
            creates=[c.kwargs for c in self.trace_creates.call_args_list if c.kwargs.get('name')=='chat_request']
            updates=[c.kwargs for c in self.trace_updates.call_args_list if c.kwargs.get('name')=='chat_request']
            self.assertEqual(len(creates),1);self.assertEqual(str(creates[0]['id']),trace_id)
            self.assertEqual(len(updates),1);self.assertEqual(str(updates[0]['run_id']),trace_id)
            self.assertIsNotNone(updates[0]['end_time'])
            self.assertEqual(updates[0]['outputs']['validation_status'],'passed')
            self.assertFalse(updates[0]['error'])

    async def test_disabled_tracing_does_not_fabricate_trace_ids(self):
        with self.tracing(enabled=False):
            events=await self.stream('disabled');final=events[-1]
            self.assertIsNone(final.get('langsmith_run_id'))
            self.assertIsNone(self.store.get_investigation(final['run_id'],'disabled')['langsmith_run_id'])
            self.assertIsNone(get_current_run_tree())

    async def test_concurrent_streams_keep_distinct_trace_ids_and_contexts(self):
        with self.tracing() as runs:
            a,b=await asyncio.gather(self.stream('a'),self.stream('b'))
            aid,bid=a[-1].get('langsmith_run_id'),b[-1].get('langsmith_run_id')
            self.assertTrue(aid);self.assertTrue(bid);self.assertNotEqual(aid,bid)
            for thread,events in [('a',a),('b',b)]:
                final=events[-1];trace_id=final['langsmith_run_id']
                self.assertEqual(runs[trace_id].inputs['thread_id'],thread)
                self.assertEqual(self.store.get_investigation(final['run_id'],thread)['langsmith_run_id'],trace_id)
            self.assertIsNone(get_current_run_tree())

if __name__=='__main__':unittest.main()
