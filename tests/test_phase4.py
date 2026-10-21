"""Grounding regressions with fixed observations; no live model or user database."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'eval')]
os.environ.update(LLM_BACKEND='fake', LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
from agent.report import evidence_catalog, validate_draft, build_report, render_report
from agent.fake_llm import FastLLM, QualityLLM
from agent.deps import SupervisorDeps
from agent.graph import build_graph
from agent.session_store import SqliteSessionStore
from harness import FixedMCP


class GroundingTests(unittest.TestCase):
    def setUp(self):
        observations = json.loads((ROOT/'tests/fixtures/monitoring.json').read_text())
        self.collected = [{'source':'monitoring', 'data':observations[name]} for name in ('payments-service','gateway-service')]
        self.collected += [{'source':'runbook','target':'cpu-spike','incident_type':'cpu-spike','doc_id':'cpu-spike.md',
            'uri':'runbook://incident-response/cpu-spike','related_systems':['payments-service'],
            'data':(ROOT/'data/runbooks/cpu-spike.md').read_text(),'ok':True}]
        self.targets=['payments-service','gateway-service']
        self.draft = {'analysis':[{'system_name':name,'hypothesis':'관측된 지표와 증상의 관련성을 추가 확인해야 합니다.',
            'evidence_ids':['obs:'+name], 'claims':[{'evidence_id':'obs:'+name,'field':'cpu_percent','value':observations[name]['cpu_percent']}]} for name in self.targets],
            'actions':[{'system_name':'payments-service','document_id':'doc:cpu-spike','step_index':3,
                'evidence_ids':['obs:payments-service','doc:cpu-spike'],'condition_verified':False}]}

    def check(self,draft=None,collected=None):
        return validate_draft(json.dumps(self.draft if draft is None else draft,ensure_ascii=False),
                              self.collected if collected is None else collected,self.targets)

    def test_valid_report_uses_original_values_sources_and_conditional_manual_text(self):
        draft,errors=self.check();self.assertEqual(errors,[])
        report=build_report(self.collected,self.targets,'passed',draft)
        text=render_report(report)
        for heading in ('1) 관측값','2) 분석','3) 권장 조치','4) 미확인 사항','5) 출처'):self.assertIn(heading,text)
        self.assertIn('91.0%',text);self.assertIn('cpu-spike.md',text);self.assertIn('runbook://',text)
        self.assertIn('90%를 넘고 지속되면',report['actions'][0]['text'])
        self.assertFalse(report['actions'][0]['condition_verified']);self.assertIn('미확인',report['actions'][0]['condition'])

    def test_wrong_cpu_value_rejected_with_expected_field_and_source(self):
        self.draft['analysis'][0]['claims'][0]['value']=1
        errors=self.check()[1]
        self.assertTrue(any('payments-service' in e and 'cpu_percent' in e and '91.0' in e for e in errors))

    def test_gateway_evidence_cannot_be_attached_to_payments(self):
        self.draft['analysis'][0]['evidence_ids']=['obs:gateway-service']
        self.draft['analysis'][0]['claims'][0]['evidence_id']='obs:gateway-service'
        self.assertTrue(self.check()[1])

    def test_missing_document_and_cross_service_action_rejected(self):
        for field,value in [('document_id','doc:invented'),('system_name','gateway-service'),('step_index',999),('condition_verified',True)]:
            draft=copy.deepcopy(self.draft);draft['actions'][0][field]=value
            with self.subTest(field=field):self.assertTrue(self.check(draft)[1])

    def test_free_text_numbers_asserted_normality_duration_and_other_system_rejected(self):
        for text in ('CPU는 1%입니다','정상입니다','CPU 부하가 지속되고 있습니다','gateway-service 패킷 손실이 원인일 가능성'):
            draft=copy.deepcopy(self.draft);draft['analysis'][0]['hypothesis']=text
            with self.subTest(text=text):self.assertTrue(self.check(draft)[1])

    def test_unknown_and_failed_service_not_normal_or_zero_observation(self):
        data={'system_name':'payments-service','status':'unknown','cpu_percent':0,'memory_percent':0,'alerts':[]}
        collected=[{'source':'monitoring','data':data},{'source':'monitoring_failure','target':'gateway-service','data':'조회 실패'}]
        self.assertTrue(self.check(collected=collected)[1])
        report=build_report(collected,self.targets,'unverified')
        self.assertIsNone(report['observations'][0]['cpu_percent'])
        self.assertNotIn('CPU 0%',render_report(report));self.assertEqual(report['actions'],[])
        self.assertIn('gateway-service',render_report(report))

    def test_missing_section_bool_measurement_unknown_field_and_analysis_coverage(self):
        candidates=[]
        for name in ('analysis','actions'):
            draft=copy.deepcopy(self.draft);del draft[name];candidates.append(draft)
        draft=copy.deepcopy(self.draft);draft['analysis'][0]['claims'][0]['value']=True;candidates.append(draft)
        draft=copy.deepcopy(self.draft);draft['analysis'][0]['claims'][0]['field']='packet_loss';candidates.append(draft)
        draft=copy.deepcopy(self.draft);draft['analysis'].pop();candidates.append(draft)
        for draft in candidates:self.assertTrue(self.check(draft)[1])

    def test_measurement_and_alert_threshold_are_not_interchangeable(self):
        # Runbook threshold is 90%; current measured CPU is 91%.
        self.draft['analysis'][0]['claims'][0]['value']=90
        self.assertTrue(self.check()[1])
        text=render_report(build_report(self.collected,self.targets,'unverified'))
        self.assertIn('활성 알림(발생 시점 미확인)',text)
        self.assertIn('시점 일치',text)

    def test_rejected_report_contains_no_rejected_hypothesis_or_action(self):
        self.draft['analysis'][0]['hypothesis']='UNSAFE_SENTINEL'
        report=build_report(self.collected,self.targets,'unverified',self.draft)
        self.assertNotIn('UNSAFE_SENTINEL',render_report(report));self.assertEqual(report['actions'],[])
        self.assertIn('출처',render_report(report));self.assertIn('91.0%',render_report(report))


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fast=FastLLM('fixed',0);self.quality=QualityLLM('fixed',0);self.mcp=FixedMCP(ROOT,{})
        self.deps=SupervisorDeps(self.fast,self.quality,self.mcp,None,'v2')

    async def ask(self,query):
        return await build_graph(self.deps).ainvoke({'query':query,'history':[],'events':[],'cost_usd':0})

    async def test_fake_rewrite_then_grounded_report_and_statuses(self):
        result=await self.ask('payments-service와 cache-service 공통 장애 진단')
        self.assertEqual(result['validation_status'],'passed')
        self.assertEqual([e['status'] for e in result['events'] if e['type']=='Validation'],['rejected','passed'])
        self.assertEqual(len(result['report']['observations']),2)
        self.assertEqual(len(result['report']['actions']),2)

    async def test_malformed_preview_is_rejected_and_rewritten_without_aborting(self):
        original=self.quality.write
        async def write(query,collected,attempt,feedback,on_token=None):
            if attempt==1:
                raw='{"analysis":[{"hypothesis":"bad' + chr(92) + 'q"}],"actions":[]}'
                if on_token:on_token(raw)
                return raw,{'usd':0}
            return await original(query,collected,attempt,feedback,on_token=on_token)
        self.quality.write=write
        result=await self.ask('payments-service 장애 진단')
        self.assertEqual(result['validation_status'],'passed')
        self.assertEqual([e['status'] for e in result['events'] if e['type']=='Validation'],['rejected','passed'])

    async def test_two_bad_drafts_preserve_sources_without_final_recommendation(self):
        self.quality.write=AsyncMock(return_value=('{"analysis":[],"actions":[]}',{'usd':0}))
        result=await self.ask('payments-service 장애 진단')
        self.assertEqual(self.quality.write.await_count,2);self.assertEqual(result['validation_status'],'unverified')
        self.assertEqual(result['report']['actions'],[]);self.assertEqual(result['report']['analysis'],[])
        self.assertIn('cpu-spike.md',result['final'])

    async def test_analysis_receives_question_service_and_memory(self):
        self.mcp.case['overrides']={'cache-service':{'system_name':'cache-service','status':'degraded','cpu_percent':10,
            'memory_percent':88.8,'alerts':['memory growth'],'last_checked':'fixed'}}
        self.mcp.monitoring['cache-service']=self.mcp.case['overrides']['cache-service']
        original=self.fast.analyze;self.fast.analyze=AsyncMock(side_effect=original)
        result=await self.ask('cache-service 메모리를 비교해줘')
        args=self.fast.analyze.await_args.args
        self.assertIn('메모리를 비교',args[0]);self.assertTrue(any(c.get('data',{}).get('memory_percent')==88.8 for c in args[1] if isinstance(c.get('data'),dict)))
        self.assertEqual(result['validation_status'],'passed')

    async def test_post_and_sse_return_same_report_contract_and_pending_before_validation(self):
        import server,httpx
        with tempfile.TemporaryDirectory() as directory:
            store=SqliteSessionStore(Path(directory)/'db.sqlite3');self.deps.session_store=store
            server.app.state.deps=self.deps;server.app.state.graph=build_graph(self.deps)
            server.app.state.llm_backend='fake';server.app.state.instance_id=1
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as client:
                post=(await client.post('/chat',json={'message':'payments-service 진단','thread_id':'post'})).json()
                sse=(await client.get('/chat/stream',params={'message':'payments-service 진단','thread_id':'sse'})).text
                events=[json.loads(line[6:]) for line in sse.splitlines() if line.startswith('data: {')]
                final=next(e for e in events if e['type']=='FinalAnswer')
                self.assertEqual(post['report'],final['report']);self.assertEqual(post['reply'],final['reply'])
                self.assertEqual(final['validation_status'],'passed')
                pending=next(i for i,e in enumerate(events) if e['type']=='AnswerToken')
                validation=next(i for i,e in enumerate(events) if e['type']=='Validation')
                self.assertEqual(events[pending]['validation_status'],'pending');self.assertLess(pending,validation)
                chat=(await client.post('/chat',json={'message':'안녕','thread_id':'chat'})).json()
                self.assertIsNone(chat['report']);self.assertIsNone(chat['metadata']['validation_status'])
            store.close()

    async def test_actual_analyzer_http_inputs_include_question_and_memory_for_both_adapters(self):
        import httpx
        from agent.openai_llm import OpenAIFastLLM
        from agent.ollama_llm import OllamaFastLLM
        collected=[{'source':'monitoring','data':{'system_name':'cache-service','status':'degraded','cpu_percent':10,'memory_percent':88.8,'alerts':[]}}]
        original=httpx.AsyncClient
        for cls,module in [(OpenAIFastLLM,'openai_llm'),(OllamaFastLLM,'ollama_llm')]:
            captured=[]
            def respond(request):
                captured.append(json.loads(request.content))
                data={'choices':[{'finish_reason':'stop','message':{'content':'미확정 가설'}}]} if module=='openai_llm' else {'done':True,'message':{'content':'미확정 가설'}}
                return httpx.Response(200,json=data)
            model=cls('unit-test',0)
            if module=='openai_llm':model.api_key='test-placeholder'
            with patch(f'agent.{module}.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(respond),**kw)):
                await model.analyze('cache-service 메모리를 비교해줘',collected)
            user=captured[0]['messages'][1]['content']
            for text in ('cache-service','메모리를 비교','88.8','memory_percent'):self.assertIn(text,user)


if __name__=='__main__':unittest.main()
