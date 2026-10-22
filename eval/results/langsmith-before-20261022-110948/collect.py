import os,json,sys,hashlib
from pathlib import Path
from datetime import datetime,timezone
from dotenv import dotenv_values
from langsmith import Client
ROOT=Path(__file__).parent; OUT=ROOT/'results'
for k,v in dotenv_values(Path(__file__).resolve().parents[3]/'.env').items():
 if v is not None:os.environ[k]=v
client=Client()
requests=json.loads((OUT/'requests.json').read_text())
rows=[]; spans=[]; assertions=[]
for req in requests:
 root=client.read_run(req['trace_id'])
 runs=list(client.list_runs(project_name='opscheck-before-20261022',trace_id=root.trace_id))
 duration=(root.end_time-root.start_time).total_seconds() if root.end_time else None
 mons=[]; docs=[]; route=None; validation=[]
 for run in sorted(runs,key=lambda x:x.start_time):
  s={k:str(getattr(run,k)) if getattr(run,k,None) is not None else None for k in ['id','parent_run_id','trace_id','start_time','end_time']}
  s.update(case=req['case'],name=run.name,run_type=run.run_type,error=run.error,elapsed_seconds=(run.end_time-run.start_time).total_seconds() if run.end_time else None)
  s['token_fields']={k:getattr(run,k,None) for k in ['prompt_tokens','completion_tokens','total_tokens','total_cost']}
  s['token_fields']={k:str(v) if v is not None else None for k,v in s['token_fields'].items()}
  if run.name=='mcp.call_tool':
   s['inputs']={k:v for k,v in (run.inputs or {}).items() if k in ('name','arguments','server')}
   s['outputs']=run.outputs
   if not run.error:
    d=run.outputs or {}
    if 'output' in d:d=d['output']
    if isinstance(d,dict) and 'system_name' in d:mons.append(d)
  if run.name=='mcp.read_resource':
   s['inputs']={k:v for k,v in (run.inputs or {}).items() if k in ('uri','server')}
   if not run.error:docs.append(s['inputs'].get('uri'))
  if run.name=='route_node':s['outputs']=run.outputs;route=(run.outputs or {}).get('route')
  if run.name in ['plan_node','validate_node','replan_check_node']:s['outputs']=run.outputs
  if run.name=='measurement.openai_http':s['outputs']=run.outputs
  spans.append(s)
 expected=['payments-service','cache-service'] if req['case']=='multiple' else ['payments-service']
 got=sorted(set(d['system_name'] for d in mons))
 calls=json.loads((OUT/'api-calls.json').read_text())[req['http_calls_before']:req['http_calls_after']]
 row={'case':req['case'],'question':req['question'],'trace_id':str(root.id),'langsmith_seconds':duration,'supporting_local_seconds':req['elapsed_seconds'],'trace_span_count':len(runs),'llm_api_calls':len(calls),'input_tokens':sum(c.get('usage',{}).get('prompt_tokens',0) for c in calls),'output_tokens':sum(c.get('usage',{}).get('completion_tokens',0) for c in calls),'total_tokens':sum(c.get('usage',{}).get('total_tokens',0) for c in calls),'route':route,'expected_targets':sorted(expected),'actual_successful_targets':got,'target_set_matches':got==sorted(expected),'observations':mons,'resource_uris':docs,'error_spans':[{'name':r.name,'error':r.error} for r in runs if r.error],'reply':req['reply'],'events':[x['event'] for x in req['events']]}
 rows.append(row)
(OUT/'langsmith-spans.json').write_text(json.dumps(spans,ensure_ascii=False,indent=2,default=str))
(OUT/'measurements.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
import csv
with (OUT/'measurements.csv').open('w',newline='') as f:
 keys=['case','question','trace_id','langsmith_seconds','llm_api_calls','input_tokens','output_tokens','total_tokens','route','expected_targets','actual_successful_targets','target_set_matches','resource_uris']
 w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
 for row in rows:w.writerow({k:json.dumps(row[k],ensure_ascii=False) if isinstance(row[k],list) else row[k] for k in keys})
print(json.dumps([{k:r[k] for k in ['case','trace_id','langsmith_seconds','llm_api_calls','total_tokens','route','actual_successful_targets','resource_uris']} for r in rows],ensure_ascii=False,indent=2))
