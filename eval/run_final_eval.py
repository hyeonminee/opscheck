"""같은 고정 입력의 노드별 전후 검사. 외부 LLM 정확도·속도 평가는 아님.

원본은 sync route/plan, 개선판은 async 계약이다. 이 평가는 모델 생성 차이를
제외하기 위해 gate/replan/research/validate 노드를 직접 검사한다.
검증기는 의미가 같은 자유형/JSON 쌍을 사용하므로 동일 문자열 비교가 아니다.
"""
import argparse
import asyncio
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def ratio(passed, total):
    return {'passed': passed, 'total': total, 'rate': round(passed/total, 4) if total else None}


def summarize(rows):
    groups = sorted({r['group'] for r in rows})
    result = {g: ratio(sum(r['passed'] for r in rows if r['group']==g), sum(r['group']==g for r in rows)) for g in groups}
    bad = [r for r in rows if r['group']=='validator' and r['expected']=='reject']
    good = [r for r in rows if r['group']=='validator' and r['expected']=='accept']
    result['incorrect_blocked'] = ratio(sum(r['passed'] for r in bad), len(bad))
    result['correct_blocked'] = ratio(sum(not r['passed'] for r in good), len(good))
    return result


async def evaluate(source):
    sys.path.insert(0, str(source))
    from agent import graph
    from harness import FixedMCP
    fixture = json.loads((ROOT/'tests/fixtures/monitoring.json').read_text())
    rows=[]
    async def record(group, name, expected, run):
        started=time.perf_counter()
        try:
            actual, passed = await run()
            row={'group':group,'id':name,'expected':expected,'actual':actual,'passed':bool(passed)}
        except Exception as e:
            row={'group':group,'id':name,'expected':expected,'error':type(e).__name__,'passed':False}
        row['elapsed_seconds']=round(time.perf_counter()-started,6)
        rows.append(row)

    scopes=[
        ('extra-target',['payments-service'],['payments-service','web-01']),
        ('missing-target',['payments-service','cache-service'],['payments-service']),
        ('duplicate-target',['payments-service'],['payments-service','payments-service']),
        ('correct-target',['payments-service'],['payments-service']),
        ('unknown-target',[],['billing-service','auth-service']),
        ('unspecified-target',[],['auth-service']),
        ('mixed-extra-missing',['gateway-service','payments-service'],['gateway-service','web-01'])]
    for name, requested, planned in scopes:
        tasks=[{'id':str(i),'agent':'research','kind':'monitoring','target':v,'status':'pending'} for i,v in enumerate(planned)]
        async def scope_run(requested=requested,tasks=tasks):
            result=await graph.gate_node({'query':' / '.join(requested),'requested_targets':requested,'needs_analysis':False,'tasks':copy.deepcopy(tasks),'events':[]},None)
            actual=[t['target'] for t in result['tasks'] if t.get('kind')=='monitoring']
            return actual, len(actual)==len(requested) and set(actual)==set(requested)
        await record('scope',name,requested,scope_run)

    cases=json.loads((ROOT/'eval/cases.json').read_text())
    for case in cases:
        if not case['expected_targets'] or case.get('missing_documents'):
            continue
        monitoring=copy.deepcopy(fixture);monitoring.update(copy.deepcopy(case.get('overrides',{})))
        collected=[{'source':'monitoring','data':monitoring[name]} for name in case['expected_targets'] if name not in case.get('fail_targets',[])]
        async def manual_run(case=case,collected=collected):
            result=await graph.replan_check_node({'query':case['query'],'requested_targets':case['expected_targets'],'collected':copy.deepcopy(collected),'tasks':[],'events':[]},None)
            actual=sorted({t['target'] for t in result.get('tasks',[]) if t.get('kind')=='runbook'})
            return actual, actual==sorted(case['expected_runbooks'])
        await record('manuals',case['id'],sorted(case['expected_runbooks']),manual_run)

    async def missing_document():
        result=await graph.replan_check_node({'query':'auth-service 진단','requested_targets':['auth-service'],
            'collected':[{'source':'monitoring','data':fixture['auth-service']},{'source':'runbook','incident_type':'high-latency','data':'# Runbook Not Found'}],
            'tasks':[],'events':[]},None)
        enough=result['events'][-1]['is_sufficient']
        return enough, enough is False
    await record('missing_document','missing-source-not-sufficient',False,missing_document)

    async def partial_failure():
        mcp=FixedMCP(source,{'fail_targets':['cache-service']})
        tasks=[{'id':v,'agent':'research','kind':'monitoring','target':v,'status':'pending'} for v in ['payments-service','cache-service']]
        result=await graph.research_node({'tasks':tasks,'events':[]},SimpleNamespace(mcp=mcp))
        good=any(c.get('source')=='monitoring' and c['data'].get('system_name')=='payments-service' for c in result['collected'])
        failed=any(c.get('source')=='monitoring_failure' and c.get('target')=='cache-service' for c in result['collected'])
        return {'success_preserved':good,'failed_target_recorded':failed},good and failed
    await record('partial_failure','one-success-one-failure',{'success_preserved':True,'failed_target_recorded':True},partial_failure)

    targets=['payments-service']
    collected=[{'source':'monitoring','data':fixture['payments-service']}]
    collected.append({'source':'runbook','incident_type':'cpu-spike','target':'cpu-spike','doc_id':'cpu-spike.md',
        'uri':'runbook://incident-response/cpu-spike','related_systems':targets,'ok':True,'data':(source/'data/runbooks/cpu-spike.md').read_text()})
    valid={'analysis':[{'system_name':'payments-service','hypothesis':'관측된 부하와 응답 지연의 관계를 추가 확인해야 합니다.',
        'evidence_ids':['obs:payments-service'],'claims':[{'evidence_id':'obs:payments-service','field':'cpu_percent','value':91.0}]}],
        'actions':[{'system_name':'payments-service','document_id':'doc:cpu-spike','step_index':1,
            'evidence_ids':['obs:payments-service','doc:cpu-spike'],'condition_verified':False}]}
    checks=[('valid-cpu','accept',valid,'1) payments-service CPU는 현재 91%입니다. 원인과 지속 시간은 추가로 확인하고 조건에 맞는 매뉴얼을 참고합니다.')]
    good=copy.deepcopy(valid);good['analysis'][0]['claims']=[{'evidence_id':'obs:payments-service','field':'status','value':'degraded'}]
    checks.append(('valid-status','accept',good,'1) payments-service 상태는 degraded입니다. 원인과 지속 시간은 추가로 확인하고 조건에 맞는 매뉴얼을 참고합니다.'))
    for name, field, value, text in [
        ('wrong-cpu','value',1,'CPU는 1%입니다'),
        ('alert-threshold-as-value','value',80,'현재 CPU는 80%입니다'),
        ('wrong-status','status','healthy','상태는 healthy입니다'),
        ('cross-service','evidence_id','obs:gateway-service','gateway-service 관측으로 payments-service의 상태를 확인했습니다'),
        ('missing-document','document_id','doc:invented','없는 문서 invented의 지침을 적용합니다'),
        ('missing-step','step_index',999,'매뉴얼의 존재하지 않는 단계 999를 수행합니다'),
        ('unverified-condition','condition_verified',True,'지속 조건이 확인되었으므로 즉시 재시작합니다')]:
        bad=copy.deepcopy(valid)
        if field=='status':bad['analysis'][0]['claims'][0].update(field='status',value=value)
        elif field in ('value','evidence_id'):bad['analysis'][0]['claims'][0][field]=value
        else:bad['actions'][0][field]=value
        checks.append((name,'reject',bad,'1) payments-service 진단 결과: '+text+'. 추가 조사 없이도 해당 내용을 근거로 운영 조치를 결정할 수 있습니다.'))
    structured = hasattr(graph,'validate_draft')
    for name,expected,draft,legacy in checks:
        async def validator_run(draft=draft,legacy=legacy,expected=expected):
            result=await graph.validate_node({'draft':json.dumps(draft,ensure_ascii=False) if structured else legacy,
                'collected':copy.deepcopy(collected),'requested_targets':targets,'validation_attempts':0,'events':[]},None)
            accepted=result.get('validation_status')=='passed' if structured else result['validation_feedback'] is None
            return 'accept' if accepted else 'reject', accepted==(expected=='accept')
        await record('validator',name,expected,validator_run)
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();source=args.source.resolve()
    os.environ.update(LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
    rows=asyncio.run(evaluate(source))
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    output={'kind':'fixed-input component checks, no external model; not end-to-end accuracy or speed',
        'source':str(source),'python':platform.python_version(),'graph_sha256':digest(source/'agent/graph.py'),
        'evaluator_sha256':digest(Path(__file__)),'cases_sha256':digest(ROOT/'eval/cases.json'),
        'fixture_sha256':digest(ROOT/'tests/fixtures/monitoring.json'),
        'repeat_count':1,'model':'none; gate inputs, observations and validation drafts are fixed',
        'scope_note':'same plans and requested targets directly injected; target extraction and LLM routing are tested separately',
        'validator_note':'same semantic faults, legacy prose vs structured JSON; NOT identical strings and NOT real LLM accuracy',
        'timing_note':'diagnostic only: original research includes synthetic 0.35s delay; no speed-up claim',
        'summary':summarize(rows),'results':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    with args.output.with_suffix('.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['group','id','expected','actual','passed','error','elapsed_seconds']);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    print(json.dumps(output['summary'],ensure_ascii=False))


if __name__=='__main__':main()
