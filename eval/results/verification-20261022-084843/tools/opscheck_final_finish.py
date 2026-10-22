import csv,hashlib,json,os,re,signal,subprocess,sys,urllib.request
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path('/Users/hyeonmin/workspace/AI mini-project')
OUT=Path(Path('/private/tmp/opscheck-final-verification-path.txt').read_text().strip())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name,data):(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')

manifest=json.loads((OUT/'manifest-start.json').read_text());deliverables=json.loads((OUT/'deliverables.json').read_text())
source_changed=[name for name,digest in manifest['tracked_hashes'].items() if not (ROOT/name).is_file() or sha(ROOT/name)!=digest]
protected={name:(ROOT/name).is_file() and sha(ROOT/name)==digest for name,digest in manifest['protected'].items()}
pids={str(port):subprocess.check_output(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],text=True).strip() for port in (8100,8600)}
preserved={'checked_utc':datetime.now(timezone.utc).isoformat(),'head_unchanged':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==manifest['commit'],
    'tracked_files_checked':len(manifest['tracked_hashes']),'tracked_files_changed':source_changed,
    'protected_files_equal':protected,'user_server_pids_unchanged':pids==manifest['user_server_pids'],
    'user_server_pids':pids,'source_edits':False,'user_restart_requested':False,'commit_or_push':False,
    'git_status':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True)}
save('preservation-final.json',preserved)

# Validate new documentation links and evidence identities before handoff.
broken=[]
for path in [Path(deliverables['report']),Path(deliverables['presentation_draft']),OUT/'README.md',OUT/'report.md',OUT/'capture-index.md']:
    for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)',path.read_text()):
        link=link.strip('<>')
        if link.startswith(('https://','http://','#')):continue
        link=re.sub(r':\d+$','',link.split('#')[0])
        target=Path(link) if link.startswith('/') else path.parent/link
        if not target.exists():broken.append({'file':str(path),'target':link})

# New results may contain only synthetic queries and sample monitoring data.
# Compare against configured secret VALUES in memory; never print those values.
from dotenv import dotenv_values
secrets=[v for k,v in dotenv_values(ROOT/'.env').items() if v and len(v)>=12 and re.search(r'KEY|TOKEN|SECRET|PASSWORD',k,re.I)]
hits=[];checked=0
paths=[*OUT.rglob('*'),Path(deliverables['report']),Path(deliverables['presentation_draft'])]
for path in paths:
    if not path.is_file() or path.suffix.lower() not in ('.json','.csv','.md','.log','.py','.txt'):continue
    checked+=1;body=path.read_text(errors='replace')
    if any(value in body for value in secrets):hits.append(str(path.relative_to(ROOT)))
save('privacy-scan.json',{'text_files_checked':checked,'secret_value_matches':len(hits),'matching_file_paths_only':hits,
    'secret_values_logged':False,'scope':'current configured secret values; not an exhaustive secret-detection proof',
    'personal_user_question_policy':'existing DB only read mode=ro; exported old records have pseudonyms/service/error summaries, not question text',
    'new_db_policy':'temporary DBs include synthetic test queries/new authorized live outputs only'})
save('artifact-link-check.json',{'broken_links':broken,'passed':not broken})

# Compact evaluator-facing rows, keeping failures separate from safe degradation.
decisions=[
 {'item':'functionality-fixed','status':'passed','reason':'Python130, Node1, fixed35, SSE26/29 turns, auth5 boundary cases; no HTTP-only grading','evidence':'automatic-summary.json; integration/'},
 {'item':'functionality-live','status':'partial_failure','reason':'2 complete diagnoses; auth compound lacks CPU document action after two writes','evidence':'live/results.csv'},
 {'item':'validator-safety','status':'passed','reason':'bad7 blocked, good0/2 falsely blocked; auth unverified fallback has no unsupported final actions','evidence':'before-after-comparison.csv; live/live-auth-compound.json'},
 {'item':'RAG-search','status':'not_implemented','reason':'rule-based six-document selection; no BM25/embeddings/index','evidence':'source-code-anchors.json'},
 {'item':'multi-agent','status':'partial_application','reason':'Supervisor role decomposition; not independent autonomous subgraphs','evidence':'live/api-calls.json; source-code-anchors.json'},
 {'item':'MCP-DI-modules-tests','status':'passed','reason':'actual stdio Tool/Resource/Prompt plus injected model/MCP/DB and unit/integration contracts','evidence':'live/mcp-calls.json; test-inventory.json'},
 {'item':'LangSmith','status':'passed','reason':'three existing traces read-only, redacted request/child/store IDs match; not quality grading','evidence':'langsmith/read-only-redacted.json'},
 {'item':'live-stream-prefix','status':'passed','reason':'actual prefixes precede write completion in 3 requests; original EOF-recorder defect disclosed','evidence':'live/streaming-timing-reanalysis.json'},
 {'item':'browser-realtime-history','status':'passed','reason':'barrier screenshots, failed retries/rewrite, 20→33 and context, actual file/API byte match, 390px no overflow','evidence':'browser/; captures/'},
 {'item':'before-after-bonus-evidence','status':'passed_with_limits','reason':'same component33/version and matched 1440 screens; no real-LLM speed/accuracy/billing comparison','evidence':'before-after-comparison.csv; browser/before-after-screen-pair.json'},
 {'item':'presentation','status':'draft_ready','reason':'7 slides, allocated280 seconds; actual rehearsal and final PPT not done','evidence':deliverables['presentation_draft']},
 {'item':'existing-submission-current','status':'failed','reason':'UI ZIP15 changed files; original ZIP16; 10-slide PPT has older107-tests and actual-model results','evidence':'submission/'},
 {'item':'deployment-real-production','status':'unverified','reason':'sample monitoring only, no production operations/load/authentication investigation','evidence':'report.md section5'},
]
save('final-decisions.json',decisions)
with (OUT/'final-decisions.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['item','status','reason','evidence']);w.writeheader();w.writerows(decisions)

# Stop only the two temporary servers created for this audit. Never signal 8100/8600.
stopped=[]
for port in (18700,18701):
    try:pid=int(subprocess.check_output(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],text=True).strip())
    except subprocess.CalledProcessError:continue
    command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True)
    assert '/private/tmp/opscheck_final_ui.py' in command,command
    os.kill(pid,signal.SIGTERM);stopped.append({'port':port,'pid':pid})
save('temporary-server-cleanup.json',{'sigterm_sent':stopped,'user_ports_touched':False})

assert not source_changed and all(protected.values()) and preserved['head_unchanged'] and preserved['user_server_pids_unchanged']
assert not hits and not broken
script=Path(__file__);(OUT/'tools'/script.name).write_text(script.read_text())
file_index=[{'path':str(p.relative_to(OUT)),'bytes':p.stat().st_size,'sha256':sha(p)} for p in OUT.rglob('*') if p.is_file()]
save('artifact-index.json',file_index)
print(json.dumps({'new_artifact_files':len(file_index),'source_files_changed':len(source_changed),'protected_equal':all(protected.values()),
    'user_server_pids_unchanged':preserved['user_server_pids_unchanged'],'secret_matches':len(hits),'broken_links':len(broken),
    'report':deliverables['report'],'presentation':deliverables['presentation_draft'],'temp_servers_signaled':len(stopped)},ensure_ascii=False))
