"""Global history: cursor pages, original IDs, and per-conversation context."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'eval')]
from agent.session_store import SqliteSessionStore
import httpx
import test_phase5 as phase5
from test_phase6 import record

class HistoryPageTests(unittest.TestCase):
    def test_cursor_pages_survive_new_insert_and_equal_timestamps_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SqliteSessionStore(Path(directory)/'db')
            for i in range(25):
                value=record(f'run-{i}','a' if i%2==0 else 'b')
                store.append_exchange(value['thread_id'],'q','a',investigation=value)
            first=store.investigation_page(limit=7)
            before=store.get_investigation('run-0','a')
            value=record('new','c');store.append_exchange('c','q','a',investigation=value)
            ids=[r['run_id'] for r in first['investigations']];cursor=first['next_cursor']
            while cursor:
                page=store.investigation_page(limit=7,before=cursor)
                ids.extend(r['run_id'] for r in page['investigations']);cursor=page['next_cursor']
            self.assertEqual(ids,[f'run-{i}' for i in reversed(range(25))])
            self.assertEqual(len(ids),len(set(ids)))
            self.assertEqual(store.get_investigation('run-0','a'),before)
            self.assertTrue(all(r['thread_id']=='a' for r in store.investigation_page('a',100)['investigations']))
            self.assertEqual(store.list_investigations('a',3),store.investigation_page('a',3)['investigations'])
            with self.assertRaises(ValueError):store.investigation_page('a',before='run-1')
            with self.assertRaises(ValueError):store.investigation_page(before='missing')
            store.close()

class GlobalHistoryAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=phase5.APITests.asyncSetUp
    asyncTearDown=phase5.APITests.asyncTearDown
    ask=phase5.APITests.ask

    async def test_two_conversations_new_context_detail_download_and_comparison_stay_original(self):
        a=await self.ask('payments-service 진단','a');arun=a['metadata']['run_id']
        b=await self.ask('cache-service 진단','b');brun=b['metadata']['run_id']
        original=self.store.get_investigation(arun,'a')
        context_a=self.store.load_context('a');context_b=self.store.load_context('b')
        all_rows=(await self.client.get('/investigations')).json()['investigations']
        self.assertEqual([r['run_id'] for r in all_rows],[brun,arun])
        self.assertEqual((await self.client.get('/investigations',params={'thread_id':'a'})).json()['investigations'][0]['run_id'],arun)
        detail=(await self.client.get('/investigations/'+arun,params={'thread_id':'a'})).json()
        self.assertEqual(detail['comparison'],original['comparison'])
        md=(await self.client.get('/investigations/'+arun+'/report.md',params={'thread_id':'a'}))
        self.assertEqual(md.status_code,200);self.assertIn(arun,md.text);self.assertNotIn(brun,md.text)
        self.assertEqual(self.store.load_context('a'),context_a);self.assertEqual(self.store.load_context('b'),context_b)
        self.assertEqual((await self.client.get('/investigations/'+arun,params={'thread_id':'b'})).status_code,404)
        before_calls=len(self.mcp.calls)
        new=await self.ask('그 서비스 다시 확인해줘','new')
        self.assertEqual(new['metadata']['route'],'clarify');self.assertEqual(len(self.mcp.calls),before_calls)
        self.assertEqual(len((await self.client.get('/investigations')).json()['investigations']),2)
        self.assertEqual((await self.client.get('/investigations',params={'thread_id':'new'})).json()['investigations'],[])
        await self.ask('그 서비스 다시 확인해줘','a')
        self.assertEqual(self.mcp.calls[-1],'payments-service')
        self.assertEqual(self.store.get_investigation(arun,'a'),original)

    async def test_cursor_api_validates_scope_limit_and_invalid_cursors(self):
        a=await self.ask(thread='a');await self.ask(thread='b')
        page=(await self.client.get('/investigations',params={'limit':1})).json()
        self.assertTrue(page['next_cursor'])
        next_page=(await self.client.get('/investigations',params={'limit':1,'before':page['next_cursor']})).json()
        self.assertEqual(next_page['investigations'][0]['run_id'],a['metadata']['run_id'])
        self.assertIsNone(next_page['next_cursor'])
        self.assertEqual((await self.client.get('/investigations',params={'before':'absent'})).status_code,422)
        self.assertEqual((await self.client.get('/investigations',params={'limit':0})).status_code,422)

    async def test_proxy_global_cursor_and_original_detail_params(self):
        import web_demo_server as proxy
        original=httpx.AsyncClient;seen=[]
        def respond(request):
            seen.append(request.url)
            return httpx.Response(200,json={'investigations':[],'next_cursor':None})
        async with original(transport=httpx.ASGITransport(app=proxy.app),base_url='http://proxy') as client:
            with patch('web_demo_server.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(respond),**kw)):
                await client.get('/investigations',params={'before':'run & old','limit':7})
                self.assertNotIn('thread_id',seen[-1].params)
                self.assertEqual(seen[-1].params['before'],'run & old')
                await client.get('/investigations/id',params={'thread_id':'original & thread'})
                self.assertEqual(seen[-1].params['thread_id'],'original & thread')

if __name__=='__main__':unittest.main()
