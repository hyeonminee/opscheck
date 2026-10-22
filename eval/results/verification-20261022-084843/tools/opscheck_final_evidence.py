import hashlib,json,os,sqlite3,sys,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
ROOT=Path('/Users/hyeonmin/workspace/AI mini-project')
OUT=Path(Path('/private/tmp/opscheck-final-verification-path.txt').read_text().strip())
sys.path.insert(0,str(ROOT))
def save(name,data):
    path=OUT/name;path.parent.mkdir(exist_ok=True,parents=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

# Reanalyze existing timestamps, without rerunning any paid request.
results=json.loads((OUT/'live/results.json').read_text());rows=[]
for r in results:
    first=next((e['at'] for e in r['actual']['events'] if e['event']['type']=='AnswerToken' and e['event'].get('delta')),None)
    completed=[e['at'] for e in r['actual']['events'] if e['event']['type']=='Progress' and e['event']['step']['stage_id']=='write' and e['event']['step']['status']=='completed']
    rows.append({'id':r['id'],'original_flag':r['prefix_before_writer_end'],'original_measurement_valid':False,
        'reason':'transport stream_ended_at was set only on iterator exhaustion; adapter stops at [DONE] and closes early, so marker absent. Original result preserved.',
        'first_text_received_at':first,'first_write_completed_at':completed[0] if completed else None,
        'prefix_before_first_write_completed':bool(first and completed and first<completed[0]),
        'lead_seconds':round(completed[0]-first,6) if first and completed else None,
        'source':'live/results.json: original per-event monotonic timestamps, actual node-completed events; not another model execution',
        'raw_sha256':sha(OUT/'live/results.json')})
save('live/streaming-timing-reanalysis.json',rows)

# Existing user records: no question text, no raw trace inputs/outputs or keys copied.
with sqlite3.connect('file:'+str(ROOT/'session.db')+'?mode=ro',uri=True) as db:
    previous=[json.loads(r[0]) for r in db.execute('SELECT result_json FROM investigations ORDER BY rowid DESC LIMIT 30')]
historical=[]
for record in previous:
    if record.get('targets')!=['auth-service'] or record.get('validation_status')!='unverified':continue
    mon=next((c['data'] for c in record.get('collected',[]) if c.get('source')=='monitoring'),{})
    historical.append({'record_pseudonym':hashlib.sha256(record['run_id'].encode()).hexdigest()[:12],
        'service':'auth-service','cpu_percent':mon.get('cpu_percent'),'validation_status':record['validation_status'],
        'validation_issues':[e.get('issues',[]) for e in record.get('events',[]) if e['type']=='Validation'],
        'raw_question_copied':False,'record_preserved':True})
save('historical/auth-failures-redacted.json',historical)

# Existing connected LangSmith traces, read-only and redacted.
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=True)
os.environ.update(LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false')
from langsmith import Client
trace_rows=[];client=Client()
try:
    for record in previous:
        if not record.get('langsmith_run_id'):continue
        try:
            root=client.read_run(record['langsmith_run_id'])
            runs=list(client.list_runs(project_name=os.environ.get('LANGSMITH_PROJECT') or os.environ.get('LANGCHAIN_PROJECT') or 'capstone-all-total-openai',trace_id=root.trace_id))
            trace_rows.append({'record_pseudonym':hashlib.sha256(record['run_id'].encode()).hexdigest()[:12],
                'root_name':root.name,'saved_trace_id_matches':str(root.id)==record['langsmith_run_id'],
                'investigation_id_matches':(root.outputs or {}).get('run_id')==record['run_id'],
                'finished':root.end_time is not None,'root_error_present':bool(root.error),'validation_status':record.get('validation_status'),
                'run_count':len(runs),'unfinished':sum(r.end_time is None for r in runs),'error_runs':sum(bool(r.error) for r in runs),
                'run_names':sorted(set(r.name for r in runs)),'user_input_copied':False,'secret_values_copied':False,'read_only':True})
        except Exception as error:trace_rows.append({'status':'environment_unverified','error_type':type(error).__name__,'read_only':True})
        if len(trace_rows)>=3:break
finally:client.close()
save('langsmith/read-only-redacted.json',trace_rows)

# Compare original submitted package bytes to current source. Do not unpack over source.
packages=[]
for path in (ROOT/'submission').glob('*.zip'):
    rows=[]
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():continue
            parts=Path(info.filename).parts
            candidates=[ROOT/Path(*parts[i:]) for i in range(len(parts))]
            current=next((p for p in candidates if p.is_file() and p.suffix in ('.py','.html','.md','.json','.sh','.txt')),None)
            if current:
                rows.append({'archive_path':info.filename,'current_path':str(current.relative_to(ROOT)),
                    'same':hashlib.sha256(archive.read(info.filename)).hexdigest()==sha(current)})
        names=archive.namelist()
        packages.append({'file':path.name,'sha256':sha(path),'compared':len(rows),'same':sum(r['same'] for r in rows),
            'different':sum(not r['same'] for r in rows),'differences':[r for r in rows if not r['same']],
            'new_features_missing':{name:not any(n.endswith(name) for n in names) for name in ('agent/progress.py','tests/test_answer_streaming.py','eval/validation_cases.v1.json')},
            'preserved':True})
save('submission/zip-current-comparison.json',packages)
slides=[];ppt=ROOT/'submission/AI_Mini_Project_Presentation.pptx'
with zipfile.ZipFile(ppt) as archive:
    names=sorted([n for n in archive.namelist() if __import__('re').fullmatch(r'ppt/slides/slide\d+\.xml',n)],key=lambda n:int(__import__('re').search(r'slide(\d+)',n).group(1)))
    for name in names:
        tree=ET.fromstring(archive.read(name));text=' '.join(t.text or '' for t in tree.iter() if t.tag.endswith('}t'))
        slides.append({'slide':int(__import__('re').search(r'slide(\d+)',name).group(1)),'text':text})
save('submission/ppt-content-inspection.json',{'file':ppt.name,'sha256':sha(ppt),'slide_count':len(slides),'slides':slides,
    'keywords':{s:any(s in r['text'] for r in slides) for s in ('Phase 9','Phase 10','전체 조사','실시간','107','130','RAG')},
    'visual_open_in_powerpoint':False,'check':'OOXML text/content and file-byte inspection; no edit or deck generation'})
print(json.dumps({'live_stream_prefix_before_completion':[r['prefix_before_first_write_completed'] for r in rows] if False else [r['prefix_before_first_write_completed'] for r in json.loads((OUT/'live/streaming-timing-reanalysis.json').read_text())],
    'old_auth_unverified_records':len(historical),'traces_checked':len(trace_rows),
    'archives':[{'file':p['file'],'different':p['different']} for p in packages],'ppt_slides':len(slides)},ensure_ascii=False))
