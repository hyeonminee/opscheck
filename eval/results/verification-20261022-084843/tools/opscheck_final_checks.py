import asyncio,copy,csv,json,os,sys,tempfile
from pathlib import Path
ROOT=Path('/Users/hyeonmin/workspace/AI mini-project')
OUT=Path(Path('/private/tmp/opscheck-final-verification-path.txt').read_text().strip())
sys.path[:0]=[str(ROOT),str(ROOT/'eval')]
os.environ.update(LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false',LLM_BACKEND='fake',PYTHONDONTWRITEBYTECODE='1')
import httpx,server
from agent.graph import build_graph
from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM,QualityLLM
from agent.session_store import SqliteSessionStore
from agent.report import validate_draft
from harness import FixedMCP
from run_validation import load_dataset,assess

def save(name,data):
    path=OUT/name;path.parent.mkdir(exist_ok=True,parents=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def connect(deps):
    server.app.state.deps=deps;server.app.state.graph=build_graph(deps)
    server.app.state.llm_backend='fake';server.app.state.instance_id='isolated-final-audit'
async def ask(deps,query,thread):
    response=await server.chat_stream(query,thread)
    raw=[c async for c in response.body_iterator]
    events=[json.loads(c[6:]) for c in raw if c.startswith('data: {')]
    final=next(e for e in reversed(events) if e['type']=='FinalAnswer')
    record=deps.session_store.get_investigation(final['run_id'],thread)
    return final,record,events,raw[-1]=='data: [DONE]\n\n'

async def main():
    records=[]
    with tempfile.TemporaryDirectory(prefix='opscheck-audit-cases-') as directory:
        store=SqliteSessionStore(Path(directory)/'test.sqlite3')
        for case in load_dataset()['cases']:
            if case['kind']!='dialogue':continue
            thread='fixed-'+case['id']
            for message in case['pre_context']['history']:store.append(thread,message['role'],message['content'])
            if case['pre_context']['session_context']:
                with store.conn:store.conn.execute('INSERT INTO session_context VALUES (?,?)',(thread,json.dumps(case['pre_context']['session_context'])))
            turns=[]
            for index,turn in enumerate(case['turns']):
                mcp=FixedMCP(ROOT,{**turn['faults'],'overrides':turn['fixed_observations']})
                deps=SupervisorDeps(FastLLM('fixed',0),QualityLLM('fixed',0),mcp,store,'v2');connect(deps)
                final,record,events,done=await ask(deps,turn['question'],thread)
                branch='clarify' if any(e['type']=='TargetConfirmationRequired' for e in events) else 'incident' if record else 'chat'
                result={'route':branch,'requested_targets':record.get('targets',[]) if record else [],
                    'processing_status':final.get('processing_status'),'validation_status':final.get('validation_status'),
                    'collected':record.get('collected',[]) if record else [],'report':final.get('report'),
                    'final':final['reply'],'session_context':store.load_context(thread)}
                checks=assess(result,mcp,turn['expected']);checks['sse_done']=done
                checks['history_rule']=(record is not None)==(turn['expected']['branch']=='incident')
                turns.append({'question':turn['question'],'expected':turn['expected'],'actual':result,
                    'monitoring_calls':mcp.calls,'checks':checks,'status':'passed' if all(checks.values()) else 'failed'})
            records.append({'id':case['id'],'kind':'fixed fake + FixedMCP + actual SSE generator + temporary SQLite',
                'turns':turns,'status':'passed' if all(t['status']=='passed' for t in turns) else 'failed'})
        store.close()
    save('integration/fixed-sse-cases.json',records)

    matrix=[];fixture=json.loads((ROOT/'tests/fixtures/monitoring.json').read_text())
    # The acceptance boundary is registered before execution and reported as document
    # selection (80%), distinct from the runbook's conditional 90%-and-duration action.
    planned=[{'cpu':cpu,'expected_manuals':['high-latency']+(['cpu-spike'] if cpu>=80 else [])} for cpu in (79.9,80.0,89.9,90.0,95.0)]
    save('criteria/auth-compound-before-execution.json',{'fixed_alerts':['p95 latency 2.4s (threshold 1.0s)','connection pool usage 92%'],
        'cases':planned,'sources':['data/runbooks/cpu-spike.md (conditional action >90% and persistence)','data/runbooks/high-latency.md (latency/pool investigation)',
        'doc/README.md and existing document-selection policy: CPU >=80% includes CPU manual; this is a heuristic, not verified recovery/scale condition'],
        'expected_complete_actions':'one valid conditional action per successful service-document pair',
        'injected_fault':'remove CPU-manual action while retaining high-latency action on both drafts; safety must reject, diagnosis completeness must fail'})
    for item in planned:
        observation=copy.deepcopy(fixture['auth-service']);observation['cpu_percent']=item['cpu']
        observation['alerts']=['p95 latency 2.4s (threshold 1.0s)','connection pool usage 92%']
        mcp=FixedMCP(ROOT,{'overrides':{'auth-service':observation}})
        deps=SupervisorDeps(FastLLM('fixed',0),QualityLLM('fixed',0),mcp,None,'v2')
        result=await build_graph(deps).ainvoke({'query':'auth-service CPU와 응답 지연 및 커넥션 풀 상태 진단','history':[],'events':[]})
        selected=next((c['data'][0]['incident_types'] for c in result['collected'] if c['source']=='runbook_selection'),[])
        action_docs=sorted(a['document_id'].removeprefix('doc:') for a in result['report']['actions'])
        expected=sorted(item['expected_manuals'])
        checks={'manuals':sorted(selected)==expected,'actions':action_docs==expected,'facts':result['report']['observations'][0]['cpu_percent']==item['cpu'],
                'complete':result['validation_status']=='passed','no_condition_assertion':all(a['condition_verified'] is False for a in result['report']['actions'])}
        matrix.append({'id':'auth-cpu-'+str(item['cpu']),'fixed_observation':observation,'expected_manuals':expected,
            'actual_manuals':sorted(selected),'actual_action_documents':action_docs,'checks':checks,'status':'passed' if all(checks.values()) else 'failed'})
    quality=QualityLLM('fixed',0);original=quality.write
    async def missing_cpu(query,collected,attempt,feedback,on_token=None):
        raw,usage=await original(query,collected,2,feedback)
        draft=json.loads(raw);draft['actions']=[a for a in draft['actions'] if a['document_id']!='doc:cpu-spike']
        raw=json.dumps(draft,ensure_ascii=False)
        if on_token:on_token(raw)
        return raw,usage
    quality.write=missing_cpu
    mcp=FixedMCP(ROOT,{'overrides':{'auth-service':{**fixture['auth-service'],'cpu_percent':85.0}}})
    result=await build_graph(SupervisorDeps(FastLLM('fixed',0),quality,mcp,None,'v2')).ainvoke({'query':'auth-service 복합 장애 진단','events':[],'history':[]})
    validations=[e for e in result['events'] if e['type']=='Validation']
    injection={'id':'auth-missing-cpu-action','fault':'both drafts omit CPU document action',
        'expected_safety':'reject then unverified; two writes; final actions empty','expected_diagnosis':'incomplete, must NOT count as quality pass',
        'actual':{'validation_status':result['validation_status'],'validations':validations,'report':result['report']},
        'safety_passed':result['validation_status']=='unverified' and len(validations)==2 and not result['report']['actions'],
        'diagnosis_complete':False,'fault_injected':True}
    save('integration/auth-compound-matrix.json',{'matrix':matrix,'injected_reproduction':injection})
    with (OUT/'integration/fixed-sse-cases.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['id','turn','status','failed_checks']);w.writeheader()
        for r in records:
            for i,t in enumerate(r['turns'],1):w.writerow({'id':r['id'],'turn':i,'status':t['status'],'failed_checks':','.join(k for k,v in t['checks'].items() if not v)})
    print(json.dumps({'sse_dialogue_cases':len(records),'passed':sum(r['status']=='passed' for r in records),
        'turns':sum(len(r['turns']) for r in records),'auth_matrix_passed':sum(r['status']=='passed' for r in matrix),'auth_matrix_total':len(matrix),
        'injected_safety_pass':injection['safety_passed'],'injected_diagnosis_complete':False}))

if __name__=='__main__':asyncio.run(main())
