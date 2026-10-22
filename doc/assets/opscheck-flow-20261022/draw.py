from pathlib import Path
import html,json,hashlib
OUT=Path(__file__).parent
E=html.escape
palette={'ink':'#203C36','muted':'#627A72','green':'#2C7461','pale':'#EDF4F0','mint':'#BDDCD0','line':'#99B5A9','red':'#A65449','rose':'#FBF0ED','white':'#FFFFFF'}
parts=[]
def text(x,y,value,size=24,weight=500,fill='ink',anchor='middle'):
 parts.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{palette.get(fill,fill)}" text-anchor="{anchor}">{E(value)}</text>')
def rect(x,y,w,h,fill='pale',stroke='none',rx=22):
 parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{palette.get(fill,fill)}" stroke="{palette.get(stroke,stroke)}" stroke-width="1.5"/>')
def path(d,kind='green',dash=False):
 col=palette[kind]
 parts.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" marker-end="url(#{kind}-arrow)"'+(' stroke-dasharray="6 6"' if dash else '')+'/>')
# Connectors are authored before the opaque nodes.
xs=[74+i*258 for i in range(7)]; y=432; w=218; h=112
for i in range(6):path(f'M {xs[i]+w} 488 H {xs[i+1]-9}',kind='line')
# General chat and clarification leave routing before planning.
path('M 146 432 V 354',kind='line')
path('M 220 432 V 386 H 465 V 354',kind='line')
# Rejected draft: one rewrite, max two drafts.
path('M 1420 544 V 612 H 1215 V 552')
text(1318,646,'반려 → 재작성 1회',23,700,'green')
text(1318,676,'총 2회 작성',18,500,'muted')
# No observations: skip model writing, preserve the reason.
path('M 957 544 V 782 H 1355',kind='red')
text(1112,724,'관측 근거 없음',22,700,'red')
text(1112,752,'LLM 작성 생략',20,500,'red')
# Failed validation is never forced to pass.
path('M 1532 544 V 729',kind='red')
text(1695,649,'2회 작성 후 미통과',23,700,'red')
# Pass into the standard report, or preserve unverified status.
text(1602,414,'통과',21,700,'green')
path('M 1840 782 H 1872 V 587 H 1742 V 552',kind='red')
text(1770,619,'미통과 상태 유지',18,600,'red')
# Short direct-response nodes.
rect(74,250,226,100)
text(187,291,'대상 확인 안내',27,700)
text(187,322,'미지정 · 미등록 서비스',18,500,'muted')
rect(352,250,226,100)
text(465,291,'일반 대화 응답',27,700)
text(465,322,'조사 없이 답변',18,500,'muted')
labels=[('질문·대상 확인','대화별 맥락 연결'),('계획·대상 검증','누락·범위 이탈 방지'),('모니터링 조회','MCP Tool · 병렬 조사'),('매뉴얼 선택·조회','서비스별 선택 · 보완 1회'),('진단 초안 작성','필요 시 비교 분석 후'),('근거 검증','대상·수치·출처·조치'),('최종 보고서','이력 저장 · 비교 · 내보내기')]
for i,(label,sub) in enumerate(labels):
 fill='green' if i==6 else 'mint' if i in [2,3,5] else 'pale'
 rect(xs[i],y,w,h,fill)
 text(xs[i]+w/2,476,label,27,750,'white' if i==6 else 'ink')
 text(xs[i]+w/2,512,sub,17.5,500,'#D8ECE4' if i==6 else 'muted')
# Terminal safe report has no unverified generated recommendations.
rect(1364,738,476,88,'rose')
text(1602,775,'미통과 · 확인 근거와 한계 안내',26,750,'red')
text(1602,805,'검증되지 않은 진단·조치는 최종 보고서에서 제외',17,500,'red')
diagram='\n'.join(parts)
parts=[]
text(76,76,'03  구조와 사용 기술',23,750,'green','start')
text(76,158,'대상 확인부터 근거 검증까지 이어지는 진단 흐름',49,800,'ink','start')
header='\n'.join(parts)
parts=[]
# Technologies are real current implementation, not aspirational RAG claims.
for x,title,body in [(76,'LangGraph · Multi-Agent','Supervisor 기반 역할 분담'),(702,'MCP · 근거 검증','Tool · Resource · Prompt / 규칙 기반 매뉴얼'),(1320,'SSE · SQLite · LangSmith','실시간 진행 · 조사 이력 · 요청 추적')]:
 parts.append(f'<path d="M {x} 894 H {x+500}" stroke="#DCE8E1" stroke-width="1.5"/>')
 text(x,937,title,25,750,'green','start')
 text(x,978,body,20,550,'ink','start')
text(76,1045,'보고서: 관측값 · 분석 · 권장 조치 · 미확인 사항 · 출처',18,550,'muted','start')
text(1840,1045,'OpsCheck',20,750,'green','end')
footer='\n'.join(parts)
defs='<defs>'+''.join(f'<marker id="{k}-arrow" markerWidth="9" markerHeight="9" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M 0 0 L 8 4 L 0 8 Z" fill="{palette[k]}"/></marker>' for k in ['green','line','red'])+'</defs>'
style='<style>text{font-family:"Apple SD Gothic Neo","Malgun Gothic","Noto Sans CJK KR",sans-serif}*{box-sizing:border-box}</style>'
def svg(body,viewbox,width,height):return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="{viewbox}" role="img"><title>OpsCheck 장애 진단 처리 흐름</title>{defs}{style}{body}</svg>'
(OUT/'opscheck-flow-slide.svg').write_text(svg('<rect width="1920" height="1080" fill="white"/>'+header+diagram+footer,'0 0 1920 1080',1920,1080))
(OUT/'opscheck-flow-diagram.svg').write_text(svg(diagram,'0 210 1920 650',1920,650))
# Each substantive branch is tied to current source evidence.
source=Path(__file__).resolve().parents[3]
evidence={'diagram_type':'workflow','source_files':{},'code_anchors':{'route+clarify+chat':'agent/graph.py:68-114','scope_gate':'agent/graph.py:163-178','parallel_failure_isolation':'agent/graph.py:182-265','rule_based_documents+one_supplement':'agent/graph.py:269-316','optional_analysis':'agent/graph.py:320-337','skip_write_without_observations':'agent/graph.py:341-350','validate+safe_final':'agent/graph.py:380-399','max_drafts_2':'agent/execution.py:10','SSE+save+trace':'server.py:243-294'},'summary_notes':['Plan/gate and research/replan_check are grouped for presentation.','The diagram omits lower-level timeout/retry wiring and the outer exception handler.','General chat/clarification do not create investigation history.','Partial failures preserve successful observations and declare limits.','The analysis node only runs when needed.','Unverified report still persists validation_status=unverified; it is not a passed result.','The viewer has English fixed UI; all authored diagram labels are Korean.'],'raster_outputs':'Sharp render of the authored SVG, 2x resolution; slide white background, diagram transparent.'}
for f in ['agent/graph.py','agent/execution.py','agent/runbook_selector.py','server.py']:
 evidence['source_files'][f]=hashlib.sha256((source/f).read_bytes()).hexdigest()
(OUT/'source-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
