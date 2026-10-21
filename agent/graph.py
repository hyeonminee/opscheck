"""v2 Supervisor — 실제 langgraph.StateGraph (Wave 4·5·6·7·8·10b·15 통합).

route → (chat 직행 | incident 파이프라인)
incident: plan(MCP Prompt 절차 반영) → gate(권위 게이트) → research(가드+병렬+재시도)
          → replan_check(부족 시 gap task 후 재조사) → analysis(needs_analysis=True일 때만
          실제 실행) → write → validate(반려 시 재작성) → finalize

모든 노드는 SupervisorDeps를 functools.partial로 주입받는 순수 함수다(Wave 13).
각 노드는 @traceable로 LangSmith에 계층 트리로 기록된다.
"""
import asyncio
import json
import re
import time
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langsmith import traceable

from .deps import SupervisorDeps
from .chat_memory import name_reply
from .retry import with_retry
from .runbook_selector import select_runbooks, valid_runbook
from .execution import MAX_DRAFTS, error_event
from .report import evidence_catalog, validate_draft, build_report, render_report


from .progress import Progress

from .targets import KNOWN_SYSTEMS, requested_targets, is_target_reply


class AgentState(TypedDict, total=False):
    query: str
    requested_targets: list[str]
    session_context: dict
    thread_id: str
    history: list[dict]
    route: str
    needs_analysis: bool
    tasks: list[dict]
    gate_removed: int
    collected: list[dict]
    replan_rounds: int
    runbook_limitations: list[str]
    retry_count: int
    parallel_elapsed: float
    draft: str
    validated_draft: dict
    report: dict
    validation_attempts: int
    validation_feedback: str
    final: str
    processing_status: str
    validation_status: str
    last_prompt: str
    cost_usd: float
    events: list[dict]
    progress: list[dict]


def _cost(state: AgentState, usage: dict) -> float:
    return round(state.get("cost_usd", 0.0) + usage["usd"], 6)


@traceable(name="route_node")
async def route_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """대상·확인 대화는 코드로 통제하고 일반 분류만 모델에 맡긴다."""
    query = state["query"]
    context = dict(state.get("session_context", {}))
    targets, unknown = requested_targets(query)
    pending = context.get("pending_query")
    if pending and is_target_reply(query):
        query = pending + " / 조사 대상: " + query
        targets, unknown = requested_targets(query)
    followup = bool(re.search(r"그\s*(?:서비스|시스템)|다시\s*(?:확인|조사)|복구됐", query))
    if followup and not targets and not unknown:
        previous = context.get("last_targets", [])
        if len(previous) == 1:
            targets = previous
            query += " / 조사 대상: " + previous[0]
    name_chat = name_reply(query, []) is not None
    if name_chat and not targets and not unknown:
        result, usage = {"route": "chat"}, {"usd": 0.0}
    elif targets or unknown or followup:
        result, usage = {"route": "incident"}, {"usd": 0.0}
    else:
        result, usage = await deps.fast_llm.route(query)
        if re.search(r"장애|진단|느려|느리|지연|급증|타임아웃|저하|오류|원인|모니터링", query):
            result = {"route": "incident"}
    route = result["route"]
    events = state.get("events", []) + [{"type": "RouteDecided", "route": route}]
    output = {"query": query, "route": route, "requested_targets": targets,
              "cost_usd": _cost(state, usage), "events": events}
    if route == "incident" and (unknown or not targets):
        if unknown:
            reply = f"미등록 서비스: {', '.join(unknown)}. 다른 서비스로 대체하지 않았습니다. "
        else:
            reply = "조사할 서비스를 확인해 주세요. "
        reply += "등록된 서비스: " + ", ".join(sorted(KNOWN_SYSTEMS))
        symptom = query
        for name in unknown:
            symptom = re.sub(re.escape(name), "", symptom, flags=re.I)
        context["pending_query"] = symptom
        output.update(route="clarify", final=reply,
                      events=events + [{"type": "TargetConfirmationRequired", "unknown": unknown},
                                       {"type": "Finalize", "pipeline": "target-confirmation"}])
    else:
        context.pop("pending_query", None)
        if route == "incident":
            context["last_targets"] = targets
    output["session_context"] = context
    return output


@traceable(name="chat_answer_node")
async def chat_answer_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 5 direct 경로 — 히스토리 기반 응답 (Wave 12 세션 증명 지점).

    토큰 단위 스트리밍 — get_stream_writer()는 /chat/stream처럼 custom 모드로 소비될
    때만 실제로 전송되고, /chat(ainvoke)처럼 스트리밍하지 않는 호출에서는 자동으로
    no-op이 된다."""
    reply = name_reply(state["query"], state.get("history", []))
    if reply is not None:
        return {"final": reply, "last_prompt": None,
                "events": state["events"] + [
                    {"type": "ChatMemoryAnswered", "source": "user-history"},
                    {"type": "Finalize", "pipeline": "chat-memory"}]}
    writer = get_stream_writer()
    reply, usage = await deps.fast_llm.chat(
        state["query"], state.get("history", []),
        on_token=lambda piece: writer({"type": "AnswerToken", "delta": piece}))
    return {"final": reply, "last_prompt": usage.get("prompt"),
            "cost_usd": _cost(state, usage),
            "events": state["events"] + [{"type": "Finalize", "pipeline": "chat-direct"}]}


@traceable(name="plan_node")
async def plan_node(state: AgentState, deps: SupervisorDeps, progress=None) -> dict:
    """Wave 4 — Plan/Task 분해. Wave 2의 MCP Prompt가 표준 절차를 깔아준다.

    resource와 마찬가지로 이름을 어느 서버가 들고 있는지 미리 가정하지 않고,
    find_prompt()로 연결된 서버들을 먼저 조회해 찾은 뒤에만 get_prompt를 보낸다."""
    prompt_server, server_retries = await with_retry(deps.mcp.find_prompt, "incident_analysis_prompt",
        on_attempt=progress.callback('plan:prompt-server','진단 절차 서버 탐색') if progress else None)
    if progress:
        progress.set('plan:prompt-server','진단 절차 서버 탐색','completed',attempt=server_retries+1)
    procedure, prompt_retries = await with_retry(deps.mcp.get_prompt,
        "incident_analysis_prompt",
        {"system_name": ", ".join(state["requested_targets"]), "symptom": state["query"]},
        server=prompt_server,
        on_attempt=progress.callback('plan:prompt','진단 절차 조회') if progress else None,
    )
    if progress:
        progress.set('plan:prompt','진단 절차 조회','completed',attempt=prompt_retries+1)
    result, usage = await deps.fast_llm.plan(state["query"], procedure)
    return {"tasks": result["tasks"], "needs_analysis": result["needs_analysis"],
            "cost_usd": _cost(state, usage)}


@traceable(name="gate_node")
async def gate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """모델 계획의 누락·중복·범위 이탈을 실행 전에 보정한다."""
    tasks = state["tasks"]
    targets = state.get("requested_targets", requested_targets(state["query"])[0])
    kept = [{"id": f"task-monitoring-{i}", "agent": "research", "kind": "monitoring",
             "target": target, "status": "pending"} for i, target in enumerate(targets)]
    analyses = [t for t in tasks if t.get("agent") == "analysis"]
    needs_analysis = state.get("needs_analysis") is True
    if needs_analysis:
        kept.append({"id": "task-analysis", "agent": "analysis", "kind": "pattern-analysis",
                     "status": "pending"})
    removed = max(0, len(analyses) - int(needs_analysis))
    events = [{"type": "GateApplied", "removed_analysis_tasks": removed},
              {"type": "PlanCreated", "task_count": len(kept), "targets": targets}]
    return {"tasks": kept, "gate_removed": removed,
            "events": state["events"] + events}


@traceable(name="research_node")
async def research_node(state: AgentState, deps: SupervisorDeps, progress=None) -> dict:
    """Wave 15 가드 + Wave 10b 병렬 + Wave 9 재시도 + Wave 2 Tool/Resource 실호출."""
    tasks = state["tasks"]
    research_indices = [i for i, t in enumerate(tasks)
                        if t["agent"] == "research" and t["status"] == "pending"]
    # Wave 15 — 객체가 아니라 '실행할 목록'을 검사한다 (not research_agent 버그의 수정판)
    if not research_indices:
        return {"events": state["events"] +
                [{"type": "ResearchSkipped", "reason": "pending research task 0개"}]}

    retry_total = state.get("retry_count", 0)

    for idx in research_indices:
        task = tasks[idx]
        if task.get("kind") not in ("monitoring", "runbook") or not task.get("target"):
            raise ValueError("Invalid research task")
        if task["kind"] == "monitoring" and task["target"] not in KNOWN_SYSTEMS:
            raise ValueError("Unregistered research target")

    async def run_one(idx: int) -> dict:
        task = tasks[idx]
        retries = 0
        stage = task['kind'] + ':' + task['target']
        label = task['target'] + (' 모니터링 조회' if task['kind']=='monitoring' else ' 대응 매뉴얼 조회')
        attempt = 1
        def notify(index, status):
            nonlocal attempt
            attempt = index + 1
            if progress:
                progress.set(stage,label,status,attempt=attempt)
        try:
            if task["kind"] == "monitoring":
                data, retries = await with_retry(deps.mcp.call_tool, "get_monitoring_status", {"system_name": task["target"]}, on_attempt=notify)
                if progress:
                    progress.set(stage,label,'completed',attempt=attempt)
                return {"idx": idx, "source": "monitoring", "data": data, "retries": retries}
            discovery = stage + ':server'
            (server, template), count = await with_retry(deps.mcp.find_resource_template, "get_incident_runbook",
                on_attempt=progress.callback(discovery,task['target']+' 매뉴얼 서버 탐색') if progress else None)
            if progress:
                progress.set(discovery,task['target']+' 매뉴얼 서버 탐색','completed',attempt=count+1)
            retries += count
            uri = template.format(incident_type=task["target"])
            data, count = await with_retry(deps.mcp.read_resource, uri, server=server, on_attempt=notify)
            if progress:
                progress.set(stage,label,'completed' if valid_runbook(data) else 'failed',attempt=attempt)
            return {"idx": idx, "source": "runbook", "data": data, "uri": uri, "retries": retries + count}
        except Exception as error:
            if progress:
                progress.set(stage,label,'failed',attempt=attempt)
            # Independent lookup failures become explicit results; cancellation still propagates.
            return {"idx": idx, "source": task["kind"] + "_failure", "ok": False,
                    "data": "모니터링을 조회하지 못했습니다." if task["kind"] == "monitoring" else "대응 매뉴얼을 조회하지 못했습니다.",
                    "error": error_event(error)["code"], "retries": retries + getattr(error, "retry_count", 0)}

    started = time.perf_counter()
    results = await asyncio.gather(*(run_one(i) for i in research_indices))
    elapsed = round(time.perf_counter() - started, 2)

    new_tasks = [dict(t) for t in tasks]
    collected = list(state.get("collected", []))
    events = list(state["events"])
    for r in results:
        task = new_tasks[r["idx"]]
        item = {"source": r["source"], "data": r["data"], "target": task["target"]}
        if r.get("ok") is False:
            item.update(ok=False, error=r["error"], related_systems=task.get("related_systems", []))
        if r["source"] == "runbook":
            item.update(incident_type=task["target"], doc_id=task["target"] + ".md",
                        uri=r["uri"], related_systems=task.get("related_systems", []),
                        selection_reasons=task.get("selection_reasons", {}), ok=valid_runbook(r["data"]))
            if not item["ok"]:
                item.update(source="runbook_failure", data="대응 매뉴얼을 조회하지 못했습니다.", error="missing_document")
        task["status"] = "failed" if item.get("ok") is False else "done"
        collected.append(item)
        retry_total += r["retries"]
        events.append({"type": "TaskFailed" if task["status"] == "failed" else "TaskCompleted",
                       "id": task.get("id"), "source": task["kind"], "target": task["target"],
                       "related_systems": task.get("related_systems", []),
                       "retries": r["retries"]})

    return {"tasks": new_tasks, "collected": collected, "retry_count": retry_total,
            "parallel_elapsed": elapsed, "events": events,
            "processing_status": "partial" if any(c.get("ok") is False for c in collected) else "completed"}


@traceable(name="replan_check_node")
async def replan_check_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """서비스별 문서 누락만 최대 한 라운드 보완한다. 종료와 충분성을 구분한다."""
    collected = state.get("collected", [])
    targets = state.get("requested_targets", list(dict.fromkeys(
        c["data"]["system_name"] for c in collected if c.get("source") == "monitoring"
        and isinstance(c.get("data"), dict) and c["data"].get("system_name"))))
    selections = select_runbooks(collected, targets)
    required = {}
    for selection in selections:
        for kind in selection["incident_types"]:
            required.setdefault(kind, {})[selection["system_name"]] = selection["reasons"][kind]
    available = {c.get("incident_type") for c in collected if c.get("source") == "runbook"
                 and c.get("ok", True) and valid_runbook(c.get("data"))}
    missing = [kind for kind in required if kind not in available]
    limitations = [f"{selection['system_name']}: {issue}" for selection in selections
                   for issue in selection["limitations"]]
    invalid = any(c.get("source") in ("runbook", "runbook_failure")
                  and (c.get("ok") is False or not valid_runbook(c.get("data"))) for c in collected)
    if invalid:
        limitations.append("조회하지 못한 매뉴얼은 근거에서 제외했습니다.")
    rounds = state.get("replan_rounds", 0)
    tasks = list(state.get("tasks", []))
    more = bool(missing) and rounds < 1
    if more:
        tasks += [{"id": "task-gap-" + kind, "agent": "research", "kind": "runbook",
                   "target": kind, "status": "pending", "related_systems": list(required[kind]),
                   "selection_reasons": required[kind]} for kind in missing]
    elif missing:
        limitations.append("추가 조사 한도 후에도 없는 매뉴얼: " + ", ".join(missing))
    # Keep document text unchanged; attach service links even when it was already fetched.
    updated = []
    for item in collected:
        if item.get("source") == "runbook_selection":
            continue
        if item.get("source") == "runbook" and item.get("incident_type") in required:
            kind = item["incident_type"]
            item = {**item, "related_systems": list(required[kind]), "selection_reasons": required[kind]}
        updated.append(item)
    updated.append({"source": "runbook_selection", "data": selections})
    event = {"type": "ReplanCheck", "round": rounds,
             "is_sufficient": not missing and not limitations,
             "needs_more_research": more, "missing": missing, "limitations": limitations}
    events = state.get("events", [])
    if rounds == 0:
        events = events + [{"type": "RunbookSelection", "selections": selections}]
    return {"tasks": tasks, "collected": updated,
            "replan_rounds": rounds + int(more), "runbook_limitations": limitations,
            "events": events + [event]}


@traceable(name="analysis_node")
async def analysis_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 6 보완 — needs_analysis=True가 정당한 경우(모델의 자기모순이 아니라
    실제로 패턴 비교가 필요하다고 판단한 경우)를 위한 실행 경로. gate가 살려둔
    agent=='analysis' task가 있을 때만 수집된 monitoring 결과들을 비교 분석한다."""
    tasks = state["tasks"]
    pending = [t for t in tasks if t["agent"] == "analysis" and t["status"] == "pending"]
    if not pending or not any(c["source"] == "monitoring" for c in state.get("collected", [])):
        return {"events": state["events"] +
                [{"type": "AnalysisSkipped", "reason": "분석 작업 또는 확보한 모니터링 근거 없음"}]}
    summary, usage = await deps.fast_llm.analyze(state["query"], state.get("collected", []))
    new_tasks = [dict(t) for t in tasks]
    for t in new_tasks:
        if t["agent"] == "analysis" and t["status"] == "pending":
            t["status"] = "done"
    collected = state.get("collected", []) + [{"source": "analysis", "data": summary}]
    event = {"type": "AnalysisCompleted", "length": len(summary)}
    return {"tasks": new_tasks, "collected": collected,
            "cost_usd": _cost(state, usage), "events": state["events"] + [event]}


@traceable(name="write_node")
async def write_node(state: AgentState, deps: SupervisorDeps) -> dict:
    collected = state.get('collected', [])
    usable = any(s['kind'] == 'monitoring' and s['usable'] for s in evidence_catalog(collected).values())
    if not usable:
        reason = '모든 대상의 모니터링 조회에 실패했거나 상태를 확인하지 못했습니다.'
        report = build_report(collected, state.get('requested_targets', []), 'unverified', reason=reason)
        return {'draft': '', 'report': report, 'processing_status': 'failed',
                'validation_status': 'unverified', 'last_prompt': None,
                'events': state['events'] + [{'type': 'DiagnosisUnavailable'}]}
    attempt = state.get('validation_attempts', 0) + 1
    writer = get_stream_writer()
    writer({'type': 'AnswerToken', 'attempt': attempt, 'delta': '', 'validation_status': 'pending'})
    received, emitted = '', ''
    def preview(piece):
        nonlocal received, emitted
        received += piece
        # JSON 원문은 숨기고 미완성 가설도 복호화할 수 있는 부분만 표시한다.
        hypotheses = re.findall(r'"hypothesis"\s*:\s*("(?:[^"\\]|\\.)*(?:"|$))', received)
        texts = []
        for value in hypotheses:
            try:
                decoded = json.loads(value if value.endswith('"') else value + '"')
                decoded.encode('utf-8')  # 분할된 유니코드 surrogate는 다음 조각까지 기다린다.
                texts.append(decoded)
            except ValueError:
                break
        text = '\n'.join(texts)
        if text.startswith(emitted) and text != emitted:
            writer({'type': 'AnswerToken', 'attempt': attempt, 'delta': text[len(emitted):],
                    'validation_status': 'pending'})
            emitted = text
    draft, usage = await deps.quality_llm.write(state['query'], collected, attempt,
                        state.get('validation_feedback'), on_token=preview)
    return {'draft': draft, 'last_prompt': usage.get('prompt'), 'validation_status': 'pending',
            'cost_usd': _cost(state, usage), 'events': state['events'] + [
                {'type': 'DraftWritten', 'attempt': attempt, 'length': len(draft)}]}


@traceable(name="validate_node")
async def validate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    attempts = state.get('validation_attempts', 0) + 1
    validated, errors = validate_draft(state['draft'], state.get('collected', []), state.get('requested_targets', []))
    status = 'passed' if not errors else 'unverified' if attempts >= MAX_DRAFTS else 'rejected'
    feedback = '\n'.join(errors) if status == 'rejected' else None
    event = {'type': 'Validation', 'attempt': attempts, 'status': status, 'issues': errors,
             'verdict': '합격' if status == 'passed' else '미통과(작성 한도 도달)' if status == 'unverified' else '불합격'}
    return {'validation_attempts': attempts, 'validation_feedback': feedback,
            'validation_status': status, 'validated_draft': validated if status == 'passed' else None,
            'events': state['events'] + [event]}


@traceable(name="finalize_node")
async def finalize_node(state: AgentState, deps: SupervisorDeps) -> dict:
    status = state.get('validation_status', 'unverified')
    report = state.get('report') or build_report(state.get('collected', []), state.get('requested_targets', []),
        status, state.get('validated_draft'), None if status == 'passed' else '작성 한도 내 근거 검증을 통과하지 못했습니다.')
    return {'final': render_report(report), 'report': report,
            'events': state['events'] + [{'type': 'Finalize', 'pipeline': 'incident-v2',
                'processing_status': state.get('processing_status'), 'validation_status': status}]}


def _after_route(state: AgentState) -> str:
    return "clarify" if state["route"] == "clarify" else (
        "chat_answer" if state["route"] == "chat" else "plan")


def _after_replan_check(state: AgentState) -> str:
    last = state["events"][-1]
    return "research" if last.get("needs_more_research") else "analysis"


def _after_validate(state: AgentState) -> str:
    return "finalize" if state.get("validation_feedback") is None else "write"


def build_graph(deps: SupervisorDeps):
    g = StateGraph(AgentState)
    def node(fn):
        async def execute(state):
            progress = Progress(state)
            name = fn.__name__.removesuffix('_node')
            attempt = state.get('validation_attempts',0)+1 if name in ('write','validate') else 1
            labels = {'route':'요청 분류','chat_answer':'일반 대화 답변','plan':'계획 수립',
                      'gate':'조사 계획 확인','analysis':'관측 비교 분석','write':'답변 작성',
                      'validate':'근거 검증','finalize':'최종 답변 정리'}
            skipped = (name=='analysis' and (not any(t['agent']=='analysis' and t['status']=='pending' for t in state['tasks'])
                        or not any(c['source']=='monitoring' for c in state.get('collected',[])))) or (
                        name=='write' and not any(v['kind']=='monitoring' and v['usable'] for v in evidence_catalog(state.get('collected',[])).values()))
            if name in labels:
                progress.set(name,labels[name],'skipped' if skipped else 'running',attempt=attempt)
            try:
                delta = await fn(state,deps=deps,**({'progress':progress} if name in ('research','plan') else {}))
            except BaseException as error:
                if name in labels and not skipped:
                    progress.set(name,labels[name],'interrupted' if isinstance(error,asyncio.CancelledError) else 'failed',attempt=attempt)
                progress.finish()
                raise
            if name in labels and not skipped:
                verdict = delta.get('validation_status') if name=='validate' else None
                progress.set(name,labels[name],verdict if verdict in ('rejected','unverified') else 'completed',attempt=attempt)
            if name=='route' and delta.get('route')=='incident':
                for stage in ('plan','gate','write','validate'):
                    progress.set(stage,labels[stage],'pending')
            if name in ('gate','replan_check'):
                for task in delta.get('tasks',[]):
                    if task['agent']=='research' and task['status']=='pending':
                        stage=task['kind']+':'+task['target']
                        label=task['target']+(' 모니터링 조회' if task['kind']=='monitoring' else ' 대응 매뉴얼 조회')
                        if not any(s['stage_id']==stage for s in progress.steps):
                            progress.set(stage,label,'pending')
                if name=='gate' and any(t['agent']=='analysis' for t in delta.get('tasks',[])):
                    progress.set('analysis',labels['analysis'],'pending')
            if name=='validate' and delta.get('validation_status')=='rejected':
                for stage in ('write','validate'):
                    progress.set(stage,labels[stage],'pending',attempt=attempt+1)
            if name in ('finalize','chat_answer') or (name=='route' and delta.get('route')=='clarify'):
                progress.finish()
            return {**delta,'progress':progress.steps}
        return execute
    g.add_node("route", node(route_node))
    g.add_node("chat_answer", node(chat_answer_node))
    g.add_node("plan", node(plan_node))
    g.add_node("gate", node(gate_node))
    g.add_node("research", node(research_node))
    g.add_node("replan_check", node(replan_check_node))
    g.add_node("analysis", node(analysis_node))
    g.add_node("write", node(write_node))
    g.add_node("validate", node(validate_node))
    g.add_node("finalize", node(finalize_node))

    g.set_entry_point("route")
    g.add_conditional_edges("route", _after_route,
                            {"chat_answer": "chat_answer", "plan": "plan", "clarify": END})
    g.add_edge("chat_answer", END)
    g.add_edge("plan", "gate")
    g.add_edge("gate", "research")
    g.add_edge("research", "replan_check")
    g.add_conditional_edges("replan_check", _after_replan_check,
                            {"analysis": "analysis", "research": "research"})
    g.add_edge("analysis", "write")
    g.add_conditional_edges("write", lambda state: "finalize" if state.get("processing_status") == "failed" else "validate",
                            {"finalize": "finalize", "validate": "validate"})
    g.add_conditional_edges("validate", _after_validate,
                            {"finalize": "finalize", "write": "write"})
    g.add_edge("finalize", END)
    return g.compile()
