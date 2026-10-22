"""Exactly three authorized synthetic diagnoses, isolated DB, real configured LLM/MCP."""
import asyncio,copy,json,os,sqlite3,sys,time,hashlib
from pathlib import Path
from unittest.mock import patch
ROOT=Path('/Users/hyeonmin/workspace/AI mini-project')
OUT=Path(Path('/private/tmp/opscheck-final-verification-path.txt').read_text().strip())
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=True)
os.environ.update(LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false',PYTHONDONTWRITEBYTECODE='1')
import httpx,server
from agent.openai_llm import OpenAIFastLLM,OpenAIQualityLLM
from agent.deps import SupervisorDeps
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore

def save(name,data):
    path=OUT/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
calls=[];current_case=None
class CountStream(httpx.AsyncByteStream):
    def __init__(self,inner,row):self.inner=inner;self.row=row;self.pending=b''
    async def __aiter__(self):
        async for block in self.inner:
            self.pending+=block
            while b'\n' in self.pending:
                line,self.pending=self.pending.split(b'\n',1)
                if line.startswith(b'data: {'):
                    try:
                        data=json.loads(line[6:])
                        if data.get('usage'):self.row['usage']=data['usage']
                        for choice in data.get('choices',[]):
                            if choice.get('delta',{}).get('content'):
                                self.row['content_chunks']=self.row.get('content_chunks',0)+1
                                self.row.setdefault('first_content_at',time.monotonic())
                            if choice.get('finish_reason'):self.row['finish_reason']=choice['finish_reason']
                    except (ValueError,UnicodeError):pass
            yield block
        self.row['stream_ended_at']=time.monotonic()
    async def aclose(self):await self.inner.aclose()
class AuditTransport(httpx.AsyncBaseTransport):
    def __init__(self):self.inner=httpx.AsyncHTTPTransport(retries=0)
    async def handle_async_request(self,request):
        assert request.url.host=='api.openai.com' and request.url.path=='/v1/chat/completions'
        body=json.loads(request.content)
        row={'case':current_case,'model':body['model'],'stream':body.get('stream',False),'max_tokens':body.get('max_tokens'),
             'started_at':time.monotonic(),'usage':None}
        calls.append(row);save('live/api-calls-in-progress.json',calls)
        try:
            response=await self.inner.handle_async_request(request);row['http_status']=response.status_code
            if body.get('stream'):
                response.stream=CountStream(response.stream,row)
            else:
                data=await response.aread()
                try:
                    content=json.loads(data);row['usage']=content.get('usage');row['finish_reason']=content.get('choices',[{}])[0].get('finish_reason')
                except (ValueError,IndexError):pass
                row['response_received_at']=time.monotonic()
            return response
        except Exception as error:row['error_type']=type(error).__name__;raise
    async def aclose(self):await self.inner.aclose()

class ObservedMCP:
    def __init__(self,inner):self.inner=inner;self.events=[];self.auth_fixed=False
    def __getattr__(self,name):
        original=getattr(self.inner,name)
        if not asyncio.iscoroutinefunction(original):return original
        async def call(*args,**kwargs):
            result=await original(*args,**kwargs)
            row={'case':current_case,'capability':name,'arguments':args,'keyword_arguments':kwargs}
            if name=='call_tool':
                row['observed']=copy.deepcopy(result)
                if self.auth_fixed and result.get('system_name')=='auth-service':
                    result={**result,'cpu_percent':85.0}
                    row['fixed_cpu_override']=85.0
                row['returned']=copy.deepcopy(result)
            elif name in ('read_resource','get_prompt'):row['result_sha256']=hashlib.sha256(str(result).encode()).hexdigest()
            self.events.append(row);return result
        return call

async def main():
    global current_case
    ledger=OUT/'live/three-request-ledger.json'
    if ledger.exists():raise SystemExit('This run already has a paid-request ledger; additional execution refused')
    ledger.parent.mkdir(parents=True,exist_ok=True)
    cases=[
      {'id':'live-auth-compound','question':'auth-service 로그인이 느리고 커넥션 풀이 포화 상태이며 CPU 사용률도 높아요. 원인을 확인해줘.',
       'targets':['auth-service'],'manuals':{'auth-service':['cpu-spike','high-latency']},'fixed_auth_cpu':85.0},
      {'id':'live-multiple','question':'payments-service랑 cache-service 둘 다 저하됐는데 공통 원인이 있는지 분석해줘.',
       'targets':['payments-service','cache-service'],'manuals':{'payments-service':['cpu-spike'],'cache-service':['memory-leak']}},
      {'id':'live-text-stream','question':'payments-service 결제 응답이 느려지고 CPU가 치솟았어요, 원인 확인해주세요',
       'targets':['payments-service'],'manuals':{'payments-service':['cpu-spike']}}]
    config={'fast_model':os.environ.get('OPENAI_FAST_MODEL','gpt-4o-mini'),'quality_model':os.environ.get('OPENAI_QUALITY_MODEL','gpt-4o'),
        'diagnoses':cases,'repeat_per_case':1,'new_external_tracing':False,'actual_mcp':'original stdio ops/docs',
        'auth_override':'Only auth CPU is fixed to 85% after actual MCP call to guarantee the documented compound boundary. Other tool values retain actual sample jitter.',
        'no_application_source_change':True,'model_retries':'existing graph rewrite cap only; HTTP transport retries=0',
        'acceptance':'exact scopes/manuals/actions/facts/sources; unverified safety vs diagnosis completeness separately',
        'cost':'actual reported tokens only, code virtual USD not billing'}
    save('criteria/live-before-execution.json',config)
    actual=MCPClient(flaky_first_tool_call=True);await actual.start();mcp=ObservedMCP(actual)
    store=SqliteSessionStore(OUT/'live/synthetic-only.sqlite3')
    fast=OpenAIFastLLM(config['fast_model'],0);quality=OpenAIQualityLLM(config['quality_model'],0)
    assert fast.api_key and quality.api_key
    deps=SupervisorDeps(fast,quality,mcp,store,'v2')
    server.app.state.deps=deps;server.app.state.graph=build_graph(deps)
    server.app.state.llm_backend='openai';server.app.state.instance_id='isolated-authorized-live'
    original_client=httpx.AsyncClient
    def client(**kwargs):return original_client(transport=AuditTransport(),**kwargs)
    ledger_rows=[];results=[]
    try:
        for case in cases:
            current_case=case['id'];mcp.auth_fixed=bool(case.get('fixed_auth_cpu'))
            ledger_rows.append({'case':current_case,'state':'started','started_at':time.monotonic()});save('live/three-request-ledger.json',ledger_rows)
            events=[];started=time.monotonic();raw=[]
            with patch('agent.openai_llm.httpx.AsyncClient',side_effect=client):
                response=await server.chat_stream(case['question'],case['id'])
                async for chunk in response.body_iterator:
                    raw.append(chunk)
                    if chunk.startswith('data: {'):
                        event=json.loads(chunk[6:]);event.pop('prompt',None)
                        events.append({'at':time.monotonic(),'event':event})
            final=next(r['event'] for r in reversed(events) if r['event']['type']=='FinalAnswer')
            record=store.get_investigation(final['run_id'],case['id']);report=final.get('report') or {}
            expected_actions=sorted((s,'doc:'+d) for s,docs in case['manuals'].items() for d in docs)
            actual_actions=sorted((a['system_name'],a['document_id']) for a in report.get('actions',[]))
            actual_manuals={s['system_name']:sorted(s['incident_types']) for c in record.get('collected',[]) if c['source']=='runbook_selection' for s in c['data']}
            observed={c['data']['system_name']:c['data'] for c in record['collected'] if c['source']=='monitoring'}
            facts=all(all(o.get(k)==observed[o['system_name']].get(k) for k in ('status','cpu_percent','memory_percent','last_checked')) for o in report.get('observations',[]))
            expected_sources=sorted(['obs:'+s for s in case['targets']]+list({'doc:'+d for docs in case['manuals'].values() for d in docs}))
            checks={'targets':record['targets']==case['targets'],'facts':facts,
                'manuals':actual_manuals=={s:sorted(v) for s,v in case['manuals'].items()},
                'actions':actual_actions==expected_actions,'sources':sorted(s['id'] for s in report.get('sources',[]))==expected_sources,
                'sections':all(f'{i}) {t}' in final['reply'] for i,t in enumerate(['관측값','분석','권장 조치','미확인 사항','출처'],1)),
                'limits':bool(report.get('limitations')),'conditional':all(a['condition_verified'] is False for a in report.get('actions',[])),
                'verified':final.get('validation_status')=='passed','sse_done':raw[-1]=='data: [DONE]\n\n'}
            case_calls=[c for c in calls if c['case']==current_case]
            quality_calls=[c for c in case_calls if c['model']==config['quality_model']]
            text_events=[r for r in events if r['event']['type']=='AnswerToken' and r['event'].get('delta')]
            stream_real=bool(text_events) and bool(quality_calls) and text_events[0]['at']<quality_calls[-1].get('stream_ended_at',0)
            safety=(final.get('validation_status')=='passed' and checks['facts'] and checks['sources'] and checks['conditional']) or (final.get('validation_status')=='unverified' and not report.get('actions') and not report.get('analysis') and checks['facts'])
            result={'id':current_case,'question':case['question'],'expected':case,'actual':{'record':record,'final':final,'events':events},
                'checks':checks,'diagnosis_complete':all(checks.values()),'safety_passed':safety,
                'prefix_before_writer_end':stream_real,'answer_token_events':len(text_events),'elapsed_seconds':round(time.monotonic()-started,3),
                'api_calls':len(case_calls),'quality_calls':len(quality_calls),
                'usage_total_tokens':sum((c.get('usage') or {}).get('total_tokens',0) for c in case_calls),
                'usage_known_calls':sum(c.get('usage') is not None for c in case_calls),'no_population_accuracy_claim':True}
            results.append(result);save('live/'+current_case+'.json',result)
            ledger_rows[-1]['state']='finished';save('live/three-request-ledger.json',ledger_rows)
            print(json.dumps({k:result[k] for k in ('id','diagnosis_complete','safety_passed','prefix_before_writer_end','api_calls','usage_total_tokens')},ensure_ascii=False),flush=True)
    finally:
        save('live/api-calls.json',calls);save('live/mcp-calls.json',mcp.events);save('live/results.json',results)
        store.close();await actual.stop()
    save('live/summary.json',{'diagnoses_started':len(ledger_rows),'diagnoses_finished':len(results),
        'diagnosis_complete':sum(r['diagnosis_complete'] for r in results),'safety_passed':sum(r['safety_passed'] for r in results),
        'api_calls':len(calls),'api_tokens_reported':sum((c.get('usage') or {}).get('total_tokens',0) for c in calls),
        'usage_missing_calls':sum(c.get('usage') is None for c in calls),
        'billing_usd':'unmeasured; adapter virtual cost not billing','config':config})

if __name__=='__main__':asyncio.run(main())
