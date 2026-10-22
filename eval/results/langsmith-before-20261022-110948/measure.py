"""Measurement-only ASGI wrapper; the student source files are copied byte-for-byte.
Adds a request trace and usage telemetry, never changes planning/observations/answers.
"""
import os,sys,json,shutil,hashlib,time,uuid,contextvars
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import parse_qs
ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[2]
# 측정에 쓴 수업 제공 원본 소스 폴더. 기본값은 저장소의 baseline/original, 다른 사본은 OPSCHECK_ORIGINAL_SOURCE로 지정한다.
SOURCE=Path(os.environ.get('OPSCHECK_ORIGINAL_SOURCE',PROJECT/'baseline'/'original'))
WORK=ROOT/'original-runtime'
OUT=ROOT/'results'
OUT.mkdir(exist_ok=True)
(OUT/'captures').mkdir(exist_ok=True)
from dotenv import dotenv_values
for k,v in dotenv_values(PROJECT/'.env').items():
 if v is not None: os.environ[k]=v
os.environ.update(LLM_BACKEND='openai',SUPERVISOR_VERSION='v2',LANGCHAIN_PROJECT='opscheck-before-20261022',LANGSMITH_PROJECT='opscheck-before-20261022',LANGSMITH_TRACING='true',LANGCHAIN_TRACING_V2='true',PYTHONDONTWRITEBYTECODE='1')
if not WORK.exists():
 shutil.copytree(SOURCE,WORK,ignore=shutil.ignore_patterns('.env','.venv','__pycache__','*.db','*.db-*','.DS_Store'))
def hashes(base):
 return {str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob('*') if p.is_file() and p.suffix in ('.py','.html','.json','.md','.sh','.txt') and '__pycache__' not in p.parts}
source_hashes=hashes(SOURCE)
assert all(hashes(WORK).get(k)==v for k,v in source_hashes.items())
os.chdir(WORK)
sys.path.insert(0,str(WORK))
import httpx
from langsmith import trace,Client
from langsmith.run_helpers import get_current_run_tree
import server
from fastapi.responses import FileResponse
@server.app.get('/')
async def index(): return FileResponse(WORK/'web_chat.html')
CURRENT=contextvars.ContextVar('measurement',default=None)
original_post=httpx.post
calls=[]
def measured_post(url,*a,**kw):
 if str(url)!='https://api.openai.com/v1/chat/completions': return original_post(url,*a,**kw)
 payload=kw.get('json',{})
 info=CURRENT.get()
 start=time.perf_counter()
 record={'case':info['case'] if info else None,'model':payload.get('model'),'started_utc':datetime.now(timezone.utc).isoformat()}
 with trace('measurement.openai_http',run_type='llm',inputs={'messages':payload.get('messages')},metadata={'ls_provider':'openai','ls_model_name':payload.get('model'),'measurement_only':True}) as rt:
  try:
   response=original_post(url,*a,**kw)
   record['status']=response.status_code
   data=response.json()
   usage=data.get('usage',{})
   record['usage']=usage
   record['elapsed_seconds']=time.perf_counter()-start
   rt.end(outputs={'output':data.get('choices',[{}])[0].get('message',{}).get('content',''),'usage_metadata':{'input_tokens':usage.get('prompt_tokens',0),'output_tokens':usage.get('completion_tokens',0),'total_tokens':usage.get('total_tokens',0)}})
   return response
  except Exception as exc:
   record['error_type']=type(exc).__name__
   raise
  finally:
   calls.append(record)
   (OUT/'api-calls.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
httpx.post=measured_post
CASES=[('single','payments-service가 느려요'),('followup','그 서비스 다시 확인해줘'),('multiple','payments-service와 cache-service를 확인해줘')]
records=[]
class MeasuredApp:
 async def __call__(self,scope,receive,send):
  if scope['type']!='http' or scope.get('path')!='/chat/stream':
   return await server.app(scope,receive,send)
  params=parse_qs(scope.get('query_string',b'').decode())
  message=params.get('message',[''])[0]
  thread=params.get('thread_id',[''])[0]
  idx=len(records)
  if idx>=3 or message!=CASES[idx][1]:
   await send({'type':'http.response.start','status':409,'headers':[]})
   await send({'type':'http.response.body','body':b'Measurement permits exactly the three planned requests once.'})
   return
  case=CASES[idx][0]
  record={'case':case,'question':message,'thread_id':thread,'start_utc':datetime.now(timezone.utc).isoformat(),'events':[],'http_calls_before':len(calls)}
  records.append(record)
  token=CURRENT.set(record)
  start=time.perf_counter();buf=''
  async def capture(event):
   nonlocal buf
   if event['type']=='http.response.start':record['http_status']=event['status']
   if event['type']=='http.response.body':
    buf+=event.get('body',b'').decode()
    while '\n\n' in buf:
     chunk,buf=buf.split('\n\n',1)
     if not chunk.startswith('data: '):continue
     value=chunk[6:]
     if value=='[DONE]':record['done_at_seconds']=time.perf_counter()-start;continue
     data=json.loads(value)
     data.pop('prompt',None)
     record['events'].append({'at_seconds':time.perf_counter()-start,'event':data})
   await send(event)
  try:
   with trace('baseline_sse_measurement',inputs={'message':message,'thread_id':thread},tags=['before','student-3','one-pass'],metadata={'case':case,'measurement_only_wrapper':True,'source_sha256':hashlib.sha256((WORK/'agent/graph.py').read_bytes()).hexdigest()}) as rt:
    record['trace_id']=str(rt.id)
    await server.app(scope,receive,capture)
    record['elapsed_seconds']=time.perf_counter()-start
    record['http_calls_after']=len(calls)
    final=next((x['event'].get('reply','') for x in reversed(record['events']) if x['event'].get('type')=='FinalAnswer'),'')
    record['reply']=final
    rt.end(outputs={'reply':final,'done':'done_at_seconds' in record,'elapsed_seconds':record['elapsed_seconds']})
  except Exception as exc:
   record['error_type']=type(exc).__name__
   raise
  finally:
   CURRENT.reset(token)
   (OUT/'requests.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
manifest={'source':str(SOURCE),'source_hashes':source_hashes,'runtime_copy':str(WORK),'project':'opscheck-before-20261022','backend':'openai','fast_model':os.environ.get('OPENAI_FAST_MODEL','gpt-4o-mini'),'quality_model':os.environ.get('OPENAI_QUALITY_MODEL','gpt-4o'),'planned_cases':CASES,'planned_requests':3,'runs_per_case':1,'observation_policy':'unmodified real stdio MCP sample data with original random jitter','flaky_first_tool_call':'unmodified original default true; affects first request','original_fixed_research_delay_seconds':0.35,'timing':'LangSmith root duration is primary; local perf-counter timings are supporting evidence','no_source_changes':True,'no_existing_servers_restarted':True}
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
if __name__=='__main__':
 import uvicorn
 uvicorn.run(MeasuredApp(),host='127.0.0.1',port=18810,log_level='warning')
