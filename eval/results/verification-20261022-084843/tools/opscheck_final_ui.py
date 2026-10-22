"""Temporary actual source UI/graph with fixed dependencies and explicit barriers."""
import argparse,asyncio,copy,json,os,sys,time
from pathlib import Path
ROOT=Path('/Users/hyeonmin/workspace/AI mini-project')
OUT=Path(Path('/private/tmp/opscheck-final-verification-path.txt').read_text().strip())
parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['before','after']);mode=parser.parse_args().mode
SOURCE=ROOT/'baseline/original' if mode=='before' else ROOT
sys.path[:0]=[str(SOURCE),str(ROOT/'eval')]
os.environ.update(LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false',LLM_BACKEND='fake',PYTHONDONTWRITEBYTECODE='1')
import uvicorn,server
from fastapi.responses import FileResponse
from agent.graph import build_graph
from agent.deps import SupervisorDeps
from agent.session_store import SqliteSessionStore
from harness import FixedMCP

state={'query':'','gated':False,'cache_waiting':False,'writer_waiting':False,'writer_prefix':False}
cache_gate=asyncio.Event();write_gate=asyncio.Event();cache_gate.set();write_gate.set()
fixture=json.loads((ROOT/'tests/fixtures/monitoring.json').read_text())
class UIData(FixedMCP):
    async def call_tool(self,name,args):
        if args['system_name']=='cache-service' and state['gated']:
            state['cache_waiting']=True;await cache_gate.wait();state['cache_waiting']=False
        if args['system_name']=='cache-service' and '부분 실패' in state['query']:
            self.calls.append(args['system_name']);raise ValueError('controlled lookup failure')
        result=await super().call_tool(name,args)
        if args['system_name']=='payments-service' and '[비교 2]' in state['query']:
            result.update(cpu_percent=71.0,memory_percent=50.0,last_checked='2026-10-20T00:01:00+00:00')
        return result
mcp=UIData(SOURCE,{})
def route_data(query):
    return {'route':'incident' if any(s in query for s in fixture) or any(w in query for w in ('느려','장애','진단')) else 'chat','needs_analysis':False}
def plan_data(query):
    state['query']=query;state['gated']='실시간' in query
    if state['gated']:cache_gate.clear();write_gate.clear()
    targets=[s for s in fixture if s in query]
    if query=='payments-service와 cache-service 장애 진단':targets=['payments-service']
    return {'needs_analysis':False,'tasks':[{'id':'task-'+s,'agent':'research','kind':'monitoring','target':s,'status':'pending'} for s in targets]}
class BaseFast:
    name='fixed-planning-double';usd_per_1k=0
    def route(self,q):return route_data(q),{'usd':0}
    def plan(self,q,p):return plan_data(q),{'usd':0}
    def analyze(self,*args):return '원인은 추가 확인이 필요합니다.',{'usd':0}
    async def chat(self,q,history,on_token=None):
        reply='안녕하세요. 서비스 점검을 도와드립니다.'
        if on_token:on_token(reply)
        return reply,{'usd':0}
class CurrentFast(BaseFast):
    async def route(self,q):return super().route(q)
    async def plan(self,q,p):return super().plan(q,p)
    async def analyze(self,*args):return super().analyze(*args)
class BeforeQuality:
    name='fixed-semantic-answer-double';usd_per_1k=0
    async def write(self,query,collected,attempt,feedback,on_token=None):
        rows=[c['data'] for c in collected if c.get('source')=='monitoring']
        facts='\n'.join(f"- {d['system_name']}: CPU {d['cpu_percent']}%, 메모리 {d['memory_percent']}%" for d in rows)
        raw='1) 관측값\n'+facts+'\n\n2) 분석\n관측된 지표와 증상의 관련성을 추가 확인할 필요가 있습니다.\n\n3) 권장 조치\n조회한 매뉴얼의 조건을 확인한 후 검토하세요.'
        if on_token:on_token(raw)
        return raw,{'usd':0}
class AfterQuality:
    name='fixed-semantic-answer-double';usd_per_1k=0
    async def write(self,query,collected,attempt,feedback,on_token=None):
        from agent.fake_llm import QualityLLM
        raw,usage=await QualityLLM('controlled',0).write(query,collected,2,feedback)
        data=json.loads(raw)
        demonstration='실시간' in query or '미통과' in query or '작성중 오류' in query
        if demonstration and (attempt==1 or '미통과' in query):
            data['analysis'][0]['hypothesis']='추가 확인이 필요한 임시 가설입니다.'
            data['analysis'][0]['claims'][0].update(field='cpu_percent',value=1)
        raw=json.dumps(data,ensure_ascii=False)
        split=raw.index('hypothesis')+len('hypothesis')+15
        for i in range(0,split,2):
            if on_token:on_token(raw[i:i+2])
            await asyncio.sleep(.015 if demonstration else 0)
        if state['gated'] and attempt==1:
            state['writer_prefix']=True;state['writer_waiting']=True
            await write_gate.wait();state['writer_waiting']=False
        if '작성중 오류' in query:raise RuntimeError('controlled writer error')
        for i in range(split,len(raw),4):
            if on_token:on_token(raw[i:i+4])
            await asyncio.sleep(.012 if demonstration else 0)
        return raw,{'usd':0}

runtime=OUT/'runtime';runtime.mkdir(exist_ok=True)
store=SqliteSessionStore(runtime/(mode+'-ui.sqlite3'))
deps=SupervisorDeps(BaseFast() if mode=='before' else CurrentFast(),BeforeQuality() if mode=='before' else AfterQuality(),mcp,store,'v2')
server.app.state.deps=deps;server.app.state.graph=build_graph(deps);server.app.state.llm_backend='fake';server.app.state.instance_id='audit-'+mode

@server.app.get('/')
async def page():return FileResponse(SOURCE/'web_chat.html')
@server.app.get('/audit/control')
async def control(action:str='status'):
    if action=='release-monitoring':cache_gate.set()
    if action=='release-writer':write_gate.set()
    return {**state,'mode':mode,'source':str(SOURCE),'fixed_planning_double':True,'new_paid_calls':0}

class Capture:
    def __init__(self,app):self.app=app;self.count=0
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['path']!='/chat/stream':return await self.app(scope,receive,send)
        self.count+=1;number=self.count;events=[];pending=b''
        async def observe(message):
            nonlocal pending
            if message['type']=='http.response.body':
                pending+=message.get('body',b'')
                while b'\n\n' in pending:
                    chunk,pending=pending.split(b'\n\n',1)
                    if chunk.startswith(b'data: '):
                        value=chunk[6:].decode()
                        try:event=json.loads(value);event.pop('prompt',None)
                        except ValueError:event=value
                        events.append({'at':time.monotonic(),'event':event})
                if not message.get('more_body',False):
                    target=OUT/'browser';target.mkdir(exist_ok=True)
                    (target/f'{mode}-network-{number:02d}.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
            await send(message)
        await self.app(scope,receive,observe)

async def seed():
    if mode!='after':return
    from agent.fake_llm import QualityLLM
    from agent.report import build_report,render_report
    collected=[{'source':'monitoring','data':fixture['payments-service']}]
    draft,_=await QualityLLM('seed',0).write('payments-service 진단',collected,2,None)
    report=build_report(collected,['payments-service'],'passed',json.loads(draft))
    for i in range(25):
        record={'run_id':f'seed-{i:02d}','thread_id':'seed-a' if i%2==0 else 'seed-b','created':'2026-10-22T00:00:00+00:00',
            'query':f'페이지 검사 고정 기록 {i+1}','targets':['payments-service'],'processing_status':'completed','validation_status':'passed',
            'is_sample':True,'report':report,'reply':render_report(report),'collected':collected,'events':[],'progress':[],
            'comparison':[]}
        store.append_exchange(record['thread_id'],record['query'],record['reply'],investigation=record)
    for result in json.loads((OUT/'live/results.json').read_text()):
        record=copy.deepcopy(result['actual']['record'])
        store.append_exchange(record['thread_id'],record['query'],record['reply'],investigation=record)
async def main():
    await seed()
    print(json.dumps({'mode':mode,'port':18701 if mode=='before' else 18700,'source':str(SOURCE),'no_paid_calls':True}),flush=True)
    await uvicorn.Server(uvicorn.Config(Capture(server.app),host='127.0.0.1',port=18701 if mode=='before' else 18700,lifespan='off',log_level='warning')).serve()
    store.close()
asyncio.run(main())
