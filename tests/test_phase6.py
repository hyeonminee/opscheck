"""Two-snapshot comparison and saved Markdown: no real model or user DB."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'eval')]
os.environ.update(LLM_BACKEND='fake',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
import httpx
from agent.comparison import compare_service,compare_record,render_comparison
from agent.report import export_markdown
from agent.session_store import SqliteSessionStore
from agent.fake_llm import FastLLM,QualityLLM
from agent.deps import SupervisorDeps
from agent.graph import build_graph
from harness import FixedMCP


def observation(cpu=90,memory=80,checked='2026-10-21T00:00:00+00:00',status='degraded',alerts=None,system='payments-service'):
    return {'system_name':system,'cpu_percent':cpu,'memory_percent':memory,'last_checked':checked,
            'status':status,'alerts':['old','keep'] if alerts is None else alerts}


def record(run,thread='session',data=None,validation='passed'):
    data=observation() if data is None else data
    return {'run_id':run,'thread_id':thread,'created':'2026-10-21T00:00:00+00:00','query':'diagnose',
            'targets':['payments-service'],'processing_status':'completed','validation_status':validation,'is_sample':True,
            'collected':[{'source':'monitoring','data':data}], 'reply':'검증 통과\n\n1) 관측값\nCPU 90%\n\n2) 분석\n\n3) 권장 조치\n\n4) 미확인 사항\n\n5) 출처',
            'report':{'actions':[]}}


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=SqliteSessionStore(Path(self.temp.name)/'db')
    def tearDown(self):self.store.close();self.temp.cleanup()
    def save(self,value):self.store.append_exchange(value['thread_id'],'q','a',investigation=value)

    def test_90_to_70_is_minus_20_percentage_points_and_alert_sets(self):
        prior={'run_id':'old','observation':observation()}
        current=observation(cpu=70,memory=60,checked='2026-10-21T00:01:00+00:00',status='healthy',alerts=['new','keep','new'])
        result=compare_service('payments-service',current,prior)
        self.assertEqual(result['metrics']['cpu_percent']['delta_pp'],-20)
        self.assertEqual(result['alerts'],{'added':['new'],'resolved':['old'],'unchanged':['keep']})
        self.assertEqual(result['interval_seconds'],60)
        self.assertEqual(result['service_status'],{'previous':'degraded','current':'healthy'})
        self.assertIn('-20%p',render_comparison([result]));self.assertIn('복구 확정이 아닙니다',render_comparison([result]))

    def test_previous_is_latest_success_by_save_order_not_validation_or_created_time(self):
        self.save(record('first'))
        latest=record('unverified',data=observation(cpu=85),validation='unverified');latest['created']='2000-01-01';self.save(latest)
        failed=record('failed');failed['collected']=[{'source':'monitoring_failure','target':'payments-service','data':'failure'}];self.save(failed)
        self.save(record('unknown',data=observation(cpu=0,status='unknown')))
        self.save(record('other-service',data=observation(system='cache-service')))
        self.save(record('other-session','other'))
        prior=self.store.previous_observation('session','payments-service')
        self.assertEqual(prior['run_id'],'unverified');self.assertEqual(prior['observation']['cpu_percent'],85)

    def test_stored_cutoff_excludes_current_and_all_future_records(self):
        self.save(record('previous'));current=record('current',data=observation(cpu=70,checked='2026-10-21T00:01:00+00:00'));self.save(current)
        self.save(record('future',data=observation(cpu=1,checked='2026-10-21T00:02:00+00:00')))
        result=compare_record(current,self.store,before_run_id='current')[0]
        self.assertEqual(result['previous_run_id'],'previous');self.assertEqual(result['metrics']['cpu_percent']['delta_pp'],-20)
        self.assertIsNone(self.store.previous_observation('session','payments-service','not-stored'))

    def test_missing_or_invalid_time_and_non_increasing_time_are_not_comparable(self):
        for checked in (None,'bad-time','2026-10-21T00:00:00+00:00','2026-10-20T00:00:00+00:00'):
            result=compare_service('payments-service',observation(checked=checked),{'run_id':'old','observation':observation()})
            self.assertEqual(result['status'],'unavailable');self.assertEqual(result['metrics'],{})

    def test_naive_sample_time_is_explicitly_korean_and_mixed_offsets_match(self):
        prior={'run_id':'old','observation':observation(checked='2026-10-21T09:00:00')}
        result=compare_service('payments-service',observation(checked='2026-10-21T00:01:00+00:00'),prior)
        self.assertEqual(result['interval_seconds'],60);self.assertTrue(any('한국 시간' in s for s in result['limitations']))

    def test_invalid_metrics_and_missing_alerts_are_per_field_unavailable(self):
        for value in (None,'70',True,float('nan'),float('inf'),-1,101):
            current=observation(cpu=value,memory=70,checked='2026-10-21T00:01:00+00:00');current['alerts']=None
            result=compare_service('payments-service',current,{'run_id':'old','observation':observation()})
            self.assertIsNone(result['metrics']['cpu_percent']['delta_pp'])
            self.assertEqual(result['metrics']['memory_percent']['delta_pp'],-10);self.assertIsNone(result['alerts'])
            self.assertEqual(result['status'],'partial')

    def test_multiple_services_and_current_failure_do_not_reuse_old_as_current(self):
        self.save(record('previous'))
        current=record('new',data=observation(cpu=70,checked='2026-10-21T00:01:00+00:00'))
        current['targets']+=['cache-service'];current['collected'].append({'source':'monitoring_failure','target':'cache-service','data':'failure'})
        values=compare_record(current,self.store)
        self.assertEqual(values[0]['status'],'compared');self.assertEqual(values[1]['status'],'unavailable')
        self.assertIsNone(values[1]['current_checked']);self.assertEqual(values[1]['metrics'],{})
        self.assertTrue(any('현재' in reason for reason in values[1]['limitations']))


class APIExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import server
        self.server=server;self.temp=tempfile.TemporaryDirectory();self.store=SqliteSessionStore(Path(self.temp.name)/'db')
        self.mcp=FixedMCP(ROOT,{})
        self.deps=SupervisorDeps(FastLLM('fixed',0),QualityLLM('fixed',0),self.mcp,self.store,'v2')
        server.app.state.deps=self.deps;server.app.state.graph=build_graph(self.deps);server.app.state.llm_backend='fake';server.app.state.instance_id=1
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test')
    async def asyncTearDown(self):await self.client.aclose();self.store.close();self.temp.cleanup()
    async def ask(self,query='payments-service 진단'):
        return (await self.client.post('/chat',json={'message':query,'thread_id':'session'})).json()

    async def test_new_followup_is_fresh_and_comparison_survives_restart_and_future_runs(self):
        self.mcp.monitoring['payments-service']=observation()
        first=await self.ask();self.assertEqual(first['comparison'][0]['status'],'unavailable')
        self.mcp.monitoring['payments-service']=observation(cpu=70,checked='2026-10-21T00:01:00+00:00',alerts=['new'])
        second=await self.ask('그 서비스 다시 확인해줘')
        self.assertEqual(self.mcp.calls,['payments-service','payments-service'])
        self.assertEqual(second['comparison'][0]['previous_run_id'],first['metadata']['run_id'])
        self.assertEqual(second['comparison'][0]['metrics']['cpu_percent']['delta_pp'],-20)
        current_id=second['metadata']['run_id'];stored=copy.deepcopy(self.store.get_investigation(current_id,'session'))
        self.mcp.monitoring['payments-service']=observation(cpu=30,checked='2026-10-21T00:02:00+00:00');await self.ask()
        path=Path(self.temp.name)/'db';self.store.close();self.store=SqliteSessionStore(path);self.deps.session_store=self.store
        detail=(await self.client.get('/investigations/'+current_id,params={'thread_id':'session'})).json()
        self.assertEqual(detail['comparison'],stored['comparison']);self.assertIn('-20%p',detail['comparison_text'])

    async def test_markdown_matches_saved_report_comparison_and_headers_without_new_calls(self):
        self.mcp.monitoring['payments-service']=observation();first=await self.ask()
        initial=await self.client.get('/investigations/'+first['metadata']['run_id']+'/report.md',params={'thread_id':'session'})
        self.assertIn('runbook://',initial.text)
        self.mcp.monitoring['payments-service']=observation(cpu=70,checked='2026-10-21T00:01:00+00:00');reply=await self.ask()
        run=reply['metadata']['run_id'];path='/investigations/'+run+'/report.md';calls=list(self.mcp.calls)
        detail=(await self.client.get('/investigations/'+run,params={'thread_id':'session'})).json()
        file=await self.client.get(path,params={'thread_id':'session'})
        self.assertEqual(file.content,export_markdown(detail).encode('utf-8'))
        for heading in ('## 1. 관측값','## 2. 분석','## 3. 권장 조치','## 4. 미확인 사항','## 5. 출처'):
            self.assertIn(heading,file.text)
        self.assertIn('-20%p',file.text);self.assertIn('get_monitoring_status',file.text);self.assertIn('수업 샘플',file.text)
        self.assertIn('text/markdown',file.headers['content-type']);self.assertIn('attachment;',file.headers['content-disposition'])
        self.assertEqual(self.mcp.calls,calls)
        self.assertEqual(file.content,(await self.client.get(path,params={'thread_id':'session'})).content)
        self.assertEqual((await self.client.get(path,params={'thread_id':'other'})).status_code,404)

    async def test_legacy_phase5_record_comparison_is_bounded_and_read_only(self):
        old=record('old');current=record('current',data=observation(cpu=70,checked='2026-10-21T00:01:00+00:00'))
        for value in (old,current,record('future')):self.store.append_exchange('session','q','a',investigation=value)
        detail=(await self.client.get('/investigations/current',params={'thread_id':'session'})).json()
        self.assertEqual(detail['comparison'][0]['previous_run_id'],'old')
        self.assertNotIn('comparison',self.store.get_investigation('current','session'))

    async def test_unverified_export_has_no_rejected_action(self):
        self.deps.quality_llm.write=AsyncMock(return_value=('REJECTED_SENTINEL',{'usd':0}))
        response=await self.ask();run=response['metadata']['run_id']
        body=(await self.client.get('/investigations/'+run+'/report.md',params={'thread_id':'session'})).text
        self.assertIn('unverified',body);self.assertIn('검증 미통과',body);self.assertNotIn('REJECTED_SENTINEL',body)
        self.assertIn('권장 조치',body)

    async def test_sse_contains_comparison_and_proxy_preserves_download_bytes_headers_errors(self):
        self.mcp.monitoring['payments-service']=observation();await self.ask()
        self.mcp.monitoring['payments-service']=observation(cpu=70,checked='2026-10-21T00:01:00+00:00')
        response=await self.client.get('/chat/stream',params={'message':'다시 확인해줘','thread_id':'session'})
        events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {')]
        final=next(e for e in events if e['type']=='FinalAnswer');self.assertIn('-20%p',final['comparison_text'])
        import web_demo_server as proxy
        original=httpx.AsyncClient;seen=[]
        def upstream(request):
            seen.append(request.url)
            return httpx.Response(200,content='보고서 원문 -20%p'.encode(),headers={'content-type':'text/markdown; charset=utf-8','content-disposition':'attachment; filename="saved.md"'})
        async with original(transport=httpx.ASGITransport(app=proxy.app),base_url='http://proxy') as client:
            with patch('web_demo_server.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(upstream),**kw)):
                file=await client.get('/investigations/run/report.md',params={'thread_id':'session & one'})
                self.assertEqual(file.content,'보고서 원문 -20%p'.encode());self.assertEqual(file.headers['content-disposition'],'attachment; filename="saved.md"')
                self.assertEqual(seen[0].params['thread_id'],'session & one')
            with patch('web_demo_server.httpx.AsyncClient',side_effect=httpx.ConnectError('private sentinel')):
                response=await client.get('/investigations/run/report.md',params={'thread_id':'session'})
                self.assertEqual(response.status_code,502);self.assertNotIn('sentinel',response.text)


if __name__=='__main__':unittest.main()
