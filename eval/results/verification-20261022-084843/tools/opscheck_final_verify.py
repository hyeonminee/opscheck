"""Read-only project audit. Only new verification artifacts are written."""
import argparse,asyncio,copy,csv,hashlib,importlib.util,inspect,json,os,platform,sqlite3,subprocess,sys,time,zipfile
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path('/Users/hyeonmin/workspace/AI mini-project')
PY=ROOT/'.venv/bin/python'
POINTER=Path('/private/tmp/opscheck-final-verification-path.txt')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def outdir():return Path(POINTER.read_text().strip())
def save(name,data):
    path=outdir()/name;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def run(command,stem,timeout=180,env=None):
    destination=outdir()/'logs';destination.mkdir(exist_ok=True)
    started=datetime.now(timezone.utc).isoformat();before=time.monotonic()
    completed=subprocess.run(command,cwd=ROOT,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','LANGSMITH_TRACING':'false','LANGCHAIN_TRACING_V2':'false',**(env or {})},capture_output=True,text=True,timeout=timeout)
    (destination/(stem+'.stdout.log')).write_text(completed.stdout)
    (destination/(stem+'.stderr.log')).write_text(completed.stderr)
    row={'command':command,'cwd':str(ROOT),'started_utc':started,'elapsed_seconds':round(time.monotonic()-before,3),'exit_code':completed.returncode,'stdout':'logs/'+stem+'.stdout.log','stderr':'logs/'+stem+'.stderr.log'}
    save('commands/'+stem+'.json',row);return row
def init():
    stamp=datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')
    target=ROOT/'eval/results'/('verification-'+stamp);target.mkdir(parents=True,exist_ok=False)
    POINTER.write_text(str(target));(target/'captures').mkdir();(target/'tools').mkdir()
    (target/'tools/opscheck_final_verify.py').write_text(Path(__file__).read_text())
    files=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    manifest={'started_utc':datetime.now(timezone.utc).isoformat(),'started_local':datetime.now().astimezone().isoformat(),
      'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
      'initial_git_status':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),
      'python':subprocess.check_output([str(PY),'--version'],text=True).strip(),'platform':platform.platform(),
      'tracked_hashes':{f:sha(ROOT/f) for f in files if f and (ROOT/f).is_file()},
      'protected':{str(p.relative_to(ROOT)):sha(p) for p in [ROOT/'.env',ROOT/'session.db',*list((ROOT/'submission').glob('*.zip')),*list((ROOT/'submission').glob('*.pptx')),*list((ROOT/'eval/results').glob('phase*'))] if p.is_file()},
      'course_pdf_sha256':sha('/Users/hyeonmin/Downloads/AI-Mini-Project_20261004_2.pdf'),
      'user_server_pids':{str(port):subprocess.check_output(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],text=True).strip() for port in (8100,8600)},
      'policy':{'source_edits':False,'restart_user_servers':False,'git_mutations':False,'new_files_only':True,'paid_diagnosis_limit':3,'external_tracing':'existing_read_only; new runs disabled'}}
    save('manifest-start.json',manifest)
    dataset=json.loads((ROOT/'eval/validation_cases.v1.json').read_text())
    save('criteria/dataset-before-execution.json',dataset)
    criteria=[
      ('I1','single/multi/unknown/unspecified','requested scope equals monitoring calls; no replacement or omission','dataset, phase1'),
      ('I2','healthy web; payments/cache; auth CPU 79.9/80/90/95 plus latency/pool','service document sets, all successful documents linked to valid conditional actions; no default db-down','dataset, new compound fixtures'),
      ('I3','wrong value/service/source/document/step/condition; 2 invalid drafts','reject faults, accept correct structured drafts; max 2 writes and no forced approval','dataset, phase4, new compound coverage'),
      ('I4','one/all lookup failure; deadline; unknown observation','keep successful evidence, identify failures, no zero/healthy fabrication; close SSE','phase3, phase10, dataset'),
      ('I5','clarify→auth; same conversation followup; fresh conversation','retain original symptom; fresh observation; separate context; restore same temporary DB','phase1, phase5, phase9'),
      ('F1','completed/partial/unverified diagnosis','5 sections; measured facts, hypotheses, conditional actions, limits, sources; unverified must omit unsupported actions','phase4, dataset'),
      ('F2/P9','2 conversations; >20 records; select foreign record','global/current cursor scopes exact without duplicate; original thread for detail/download; active context unchanged','phase9, browser'),
      ('F3','two dated fixed observations','same-service/thread previous valid observation, exact %p and alert differences; immutable saved comparison','phase6, browser'),
      ('F4','saved record download','Markdown matches saved facts/status/sources/comparison; no new model call','phase6, browser'),
      ('P8','all regression examples plus supplemental faults','26 flow/dialogue +9 draft cases judged; 4 linked integration contracts tested separately','run_validation, mapped tests'),
      ('P10','gated slow research and writing','running and fast completion arrive before release; stable stage/attempt IDs; terminal snapshots not active','phase10, browser'),
      ('TRACE','local tracing on/off/error/cancel/concurrent; existing real traces','request root/child model+MCP/store ID match; collection != quality','sse_tracing, read-only SDK'),
      ('TEXT','gated upstream tokens; final report rendering','prefix before writer finish; pending/rejected/pass/unverified maintained; final reveal after validation; no stale/error text','answer_streaming, Node, browser'),
      ('LIVE','auth compound; payments+cache; payments SSE','exact target/doc/action sets and facts; safety and diagnosis completeness separate; exactly 3 new requests; existing rewrite cap','current OpenAI models, temporary DB, actual MCP; documented auth override'),
      ('BEFORE_AFTER','same gate plans/observations/drafts/failure','same judge and semantic inputs; component counts and error block/false block separate; no speed/cost claim','run_final_eval, matched browser screenshots')]
    save('criteria/acceptance-before-execution.json',[{'id':i,'input':q,'pass':p,'fail':'Any required set/fact/state mismatches; safety fallback does not imply answer completeness','evidence_method':e,'status':'planned'} for i,q,p,e in criteria])
    (target/'criteria/README.md').write_text('# 검증 전 확정한 판정 기준\n\n요구사항·고정 관측·매뉴얼이 정답이며 프로그램의 출력을 정답으로 녹화하지 않는다.\n가상 예시 RAG/HITL/전문 서브그래프 전체는 필수 구현으로 간주하지 않는다.\n교수님 보고서 순서·5분(준비 포함6분)·전후 화면 필수는 이번 사용자 제공 최종 지침이다.\n안전 차단과 진단 완성도는 별도 판정한다. 실제 모델 3건은 동일 질문 반복 실험이 아니다.\n')
    print(target)
def automatic():
    rows=[]
    commands=[([str(PY),'-B','-m','unittest','discover','-s','tests','-v'],'unittest'),
      (['node','tests/test_web_session.cjs'],'node'),
      ([str(PY),'-B','eval/run_validation.py','--output',str(outdir()/'validation.json')],'validation'),
      ([str(PY),'-B','eval/run_final_eval.py','--source','baseline/original','--output',str(outdir()/'before-components.json')],'before-components'),
      ([str(PY),'-B','eval/run_final_eval.py','--output',str(outdir()/'after-components.json')],'after-components')]
    for command,stem in commands:
        row=run(command,stem);rows.append(row);print(stem,row['exit_code'],flush=True)
    save('automatic-summary.json',rows)
def inventory():
    sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
    import unittest
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    def flatten(s):
        for item in s:
            if isinstance(item,unittest.TestSuite):yield from flatten(item)
            else:yield item
    rows=[]
    for test in flatten(suite):
        module=sys.modules[type(test).__module__];path=Path(module.__file__)
        method=getattr(type(test),test._testMethodName)
        if path.name in ('test_phase1.py','test_phase5.py','test_phase6.py','test_phase9.py','test_sse_tracing.py'):kind='API/storage/MCP integration (includes unit methods)'
        elif path.name in ('test_phase10.py','test_answer_streaming.py'):kind='async streaming/transport integration (includes unit methods)'
        else:kind='unit/component or fixed graph; see method'
        rows.append({'id':test.id(),'file':str(path.relative_to(ROOT)),'line':inspect.getsourcelines(method)[1],'classification':kind,'paid_model':False})
    save('test-inventory.json',rows)
    validation=json.loads((outdir()/'validation.json').read_text())
    flat=[]
    for r in validation['results']:flat.append({'id':r['id'],'status':r['status'],'reason':json.dumps(r.get('checks',r.get('test_reference')),ensure_ascii=False)})
    with (outdir()/'validation-cases.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['id','status','reason']);writer.writeheader();writer.writerows(flat)
    save('test-counts.json',{'python_methods':len(rows),'node':'one VM/stubbed DOM-logic script, not full browser E2E','dataset_automatic':validation['passed']+validation['failed'],'dataset_passed':validation['passed'],'linked_contracts':validation['linked_tests'],'note':'4 linked contracts are references to integration tests; not 4 additional auto-graded cases'})
    print('inventory',len(rows))
def refresh_index():
    import shutil
    from datetime import datetime,timezone
    for path in Path('/private/tmp').glob('opscheck_final_*.py'):
        shutil.copy2(path,outdir()/'tools'/path.name)
    deliverables=json.loads((outdir()/'deliverables.json').read_text())
    save('report-digests.json',{key:sha(Path(value)) for key,value in deliverables.items() if key in ('report','presentation_draft')})
    excluded={'artifact-index.json','evidence-integrity.json'}
    rows=[{'path':str(p.relative_to(outdir())),'bytes':p.stat().st_size,'sha256':sha(p)} for p in outdir().rglob('*') if p.is_file() and str(p.relative_to(outdir())) not in excluded]
    save('artifact-index.json',{'generated_utc':datetime.now(timezone.utc).isoformat(),'excluded':sorted(excluded),'entries':rows})
    wrong=[r['path'] for r in rows if sha(outdir()/r['path'])!=r['sha256']]
    save('evidence-integrity.json',{'indexed_files':len(rows),'hash_mismatches':wrong,'passed':not wrong,'self_generated_manifests_excluded':sorted(excluded)})
    print(json.dumps({'indexed_files':len(rows),'hash_mismatches':len(wrong)}))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action');args=parser.parse_args()
    if args.action in ('integration','live','evidence','report','finish'):
        script={'integration':'/private/tmp/opscheck_final_checks.py','live':'/private/tmp/opscheck_final_live.py','evidence':'/private/tmp/opscheck_final_evidence.py','report':'/private/tmp/opscheck_final_report.py','finish':'/private/tmp/opscheck_final_finish.py'}[args.action]
        row=run([str(PY),'-B',script],args.action,timeout=480);print(row)
    elif args.action in ('ui-after','ui-before'):
        mode=args.action.split('-')[1]
        os.execv(str(PY),[str(PY),'-B','/private/tmp/opscheck_final_ui.py',mode])
    elif args.action in ('control-release-monitoring','control-release-writer','control-status'):
        import urllib.request
        action=args.action.removeprefix('control-')
        with urllib.request.urlopen('http://127.0.0.1:18700/audit/control',timeout=3) as response:before=json.load(response)
        with urllib.request.urlopen('http://127.0.0.1:18700/audit/control?action='+action,timeout=3) as response:after=json.load(response)
        data={'at':time.monotonic(),'before':before,'after':after,'action':action}
        save('browser/'+args.action+'.json',data);print(json.dumps(data,ensure_ascii=False))
    else:globals()[args.action]()
