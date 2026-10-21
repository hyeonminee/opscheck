"""all_total 통합 실습 — FastAPI 백엔드 (Wave 1·3·12·14 축 + 전체 조립).

- Wave 1  : lifespan에서 Agent 번들(MCP 연결·LLM·그래프)을 프로세스당 정확히 1회 생성,
            app.state로 모든 요청이 공유 (creation_count/instance_id로 증명)
- Wave 2  : mcp_server.py를 subprocess(stdio)로 기동해 연결 유지
- Wave 3  : GET /chat/stream — 그래프 실행 과정을 SSE 이벤트로 스트리밍
- Wave 12 : 대화 이력을 SQLite(session.db)에 저장 — 재시작해도 유지
- Wave 14 : SUPERVISOR_VERSION(v1/v2) 환경변수로 legacy/신형 파이프라인 즉시 전환
- LangSmith: 요청당 chat_request 루트 run 아래로 노드·LLM·MCP 호출이 계층 기록됨
"""
import json
import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()   # LANGCHAIN_TRACING_V2 / API_KEY / PROJECT — import보다 먼저

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, Response
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from pydantic import BaseModel

from agent.comparison import compare_record, render_comparison
from agent.report import export_markdown
from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore
from agent.supervisor_v1 import run_v1
from agent.execution import REQUEST_TIMEOUT, failure_result

CREATION_COUNT = 0   # Wave 1 — 프로세스 내 Agent 번들 생성 횟수 (항상 1이어야 정상)


class ChatRequest(BaseModel):   # Wave 1 — Pydantic 요청 검증
    message: str
    thread_id: str = "default"


def _build_llms():
    """LLM_BACKEND=fake(기본, 결정론) | ollama(로컬 실모델) | openai(실제 OpenAI API)."""
    backend = os.environ.get("LLM_BACKEND", "fake")
    if backend == "ollama":
        from agent.ollama_llm import OllamaFastLLM, OllamaQualityLLM
        fast = OllamaFastLLM(os.environ.get("OLLAMA_FAST_MODEL", "qwen3:0.6b"),
                             usd_per_1k_tokens=0.00015)
        quality = OllamaQualityLLM(os.environ.get("OLLAMA_QUALITY_MODEL", "qwen3:8b"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    if backend == "openai":
        from agent.openai_llm import OpenAIFastLLM, OpenAIQualityLLM
        fast = OpenAIFastLLM(os.environ.get("OPENAI_FAST_MODEL", "gpt-4o-mini"),
                             usd_per_1k_tokens=0.00015)
        quality = OpenAIQualityLLM(os.environ.get("OPENAI_QUALITY_MODEL", "gpt-4o"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    fast = FastLLM("fast-model-stub", usd_per_1k_tokens=0.00015)
    quality = QualityLLM("quality-model-stub", usd_per_1k_tokens=0.005)
    return backend, fast, quality


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CREATION_COUNT
    CREATION_COUNT += 1
    mcp = MCPClient()
    await mcp.start()   # Wave 2 — MCP subprocess는 서버 수명 동안 딱 1개
    backend, fast_llm, quality_llm = _build_llms()
    app.state.llm_backend = backend
    deps = SupervisorDeps(
        fast_llm=fast_llm,
        quality_llm=quality_llm,
        mcp=mcp,
        session_store=SqliteSessionStore(),
        version=os.environ.get("SUPERVISOR_VERSION", "v2"),
    )
    app.state.deps = deps
    app.state.graph = build_graph(deps) if deps.version == "v2" else None
    app.state.instance_id = id(deps)
    yield
    await mcp.stop()
    deps.session_store.close()
    try:   # 종료 전 LangSmith 배치 전송 플러시 — kill 시 run이 pending으로 남는 것 방지
        from langchain_core.tracers.langchain import wait_for_all_tracers
        wait_for_all_tracers()
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "instance_id": app.state.instance_id,
            "creation_count": CREATION_COUNT,
            "supervisor_version": app.state.deps.version,
            "llm_backend": app.state.llm_backend,
            "fast_llm": app.state.deps.fast_llm.name,
            "quality_llm": app.state.deps.quality_llm.name}


@app.get("/version")
async def version():
    return {"supervisor_version": app.state.deps.version}


def _metadata(result: dict, run_id: str | None) -> dict:
    return {
        "run_id": result.get("run_id"),
        "route": result.get("route"),
        "task_count": len(result.get("tasks", [])),
        "gate_removed": result.get("gate_removed", 0),
        "replan_rounds": result.get("replan_rounds", 0),
        "retry_count": result.get("retry_count", 0),
        "parallel_elapsed": result.get("parallel_elapsed"),
        "validation_attempts": result.get("validation_attempts", 0),
        "cost_usd": result.get("cost_usd", 0.0),
        "llm_backend": app.state.llm_backend,
        "supervisor_version": app.state.deps.version,
        "instance_id": app.state.instance_id,
        "langsmith_run_id": run_id,
        "processing_status": result.get("processing_status", "completed"),
        "validation_status": result.get("validation_status"),
    }


def _save_result(deps, message, thread_id, result, run_id):
    """Messages/context and final investigation commit in one SQLite transaction."""
    result['run_id'] = run_id
    trace = get_current_run_tree()
    trace_id = str(trace.id) if trace else None
    record = None
    if result.get('route') == 'incident':
        record = {'run_id': run_id, 'thread_id': thread_id, 'created': datetime.now(timezone.utc).isoformat(),
            'query': message, 'resolved_query': result.get('query', message),
            'targets': result.get('requested_targets', []),
            'processing_status': result.get('processing_status', 'completed'),
            'validation_status': result.get('validation_status') or 'unverified',
            'is_sample': True, 'data_source': '수업 샘플 모니터링(MCP Tool)·대응 매뉴얼(MCP Resource)',
            'collected': [c for c in result.get('collected', []) if c.get('source') != 'analysis'],
            'report': result.get('report'), 'reply': result['final'], 'events': result.get('events', []),
            'langsmith_run_id': trace_id}
        record['comparison'] = compare_record(record, deps.session_store)
        result['comparison'] = record['comparison']
    deps.session_store.append_exchange(thread_id, message, result['final'], result.get('session_context'), investigation=record)
    return trace_id


def _log_canceled(result, run_id):
    observations = [{key: item['data'].get(key) for key in ('system_name','status','cpu_percent','memory_percent','last_checked')}
                    for item in result.get('collected', []) if item.get('source') == 'monitoring']
    logging.warning('request canceled run_id=%s sample_observations=%s', run_id, json.dumps(observations, ensure_ascii=False))


@traceable(name="chat_request")
async def _handle_chat(message: str, thread_id: str) -> tuple[dict, str | None]:
    deps = app.state.deps
    run_id = str(uuid4())
    history = deps.session_store.load(thread_id)   # Wave 12
    result = {"query": message, "thread_id": thread_id, "history": history,
              "session_context": deps.session_store.load_context(thread_id), "events": [], "cost_usd": 0.0}
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            if deps.version == "v2":
                async for update in app.state.graph.astream(result, stream_mode="updates"):
                    for delta in update.values():
                        result.update(delta)
            else:
                result = await run_v1(deps, message, thread_id, history)
    except asyncio.CancelledError:
        _log_canceled(result, run_id)
        raise
    except Exception as error:
        logging.warning("chat request failed: %s", type(error).__name__)
        result = failure_result(result, error)
    trace_id = _save_result(deps, message, thread_id, result, run_id)
    return result, trace_id


@app.get("/sessions/{thread_id}/messages")
async def session_messages(thread_id: str):
    return {"thread_id": thread_id, "messages": app.state.deps.session_store.load(thread_id)}


@app.get('/investigations')
async def investigations(thread_id: str, limit: int = Query(20, ge=1, le=100)):
    return {'investigations': app.state.deps.session_store.list_investigations(thread_id, limit)}


@app.get('/investigations/{run_id}')
async def investigation(run_id: str, thread_id: str):
    record = app.state.deps.session_store.get_investigation(run_id, thread_id)
    if record is None:
        raise HTTPException(status_code=404, detail='이 세션에서 조사 기록을 찾지 못했습니다.')
    if 'comparison' not in record:
        record['comparison'] = compare_record(record, app.state.deps.session_store, before_run_id=run_id)
    record['comparison_text'] = render_comparison(record['comparison'])
    return record


@app.get('/investigations/{run_id}/report.md')
async def investigation_markdown(run_id: str, thread_id: str):
    from urllib.parse import quote
    record = await investigation(run_id, thread_id)
    return Response(export_markdown(record), media_type='text/markdown',
                    headers={'Content-Disposition': f'attachment; filename="investigation-{quote(run_id, safe="")}.md"'})


@app.post("/chat")
async def chat(req: ChatRequest):
    result, run_id = await _handle_chat(req.message, req.thread_id)
    return {"reply": result["final"], "report": result.get("report"), "comparison": result.get("comparison"),
            "comparison_text": render_comparison(result.get("comparison", [])), "events": result.get("events", []),
            "metadata": _metadata(result, run_id)}


@app.get("/chat/stream")
async def chat_stream(message: str, thread_id: str = "default"):
    """Wave 3 — 그래프의 노드별 진행 상황을 SSE로 스트리밍.

    이벤트마다 'data: {json}\\n\\n' 형태로 내려보내고 마지막에 data: [DONE] 센티널을
    보낸다 — 프론트엔드는 \\n\\n 경계로 이벤트를 분리해 파싱해야 한다(Wave 3b).
    """
    deps = app.state.deps

    async def gen():
        run_id = str(uuid4())
        result = {"query": message, "thread_id": thread_id,
                  "history": deps.session_store.load(thread_id),
                  "session_context": deps.session_store.load_context(thread_id), "events": [], "cost_usd": 0.0}
        seen = 0
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                if deps.version != "v2":
                    result = await run_v1(deps, message, thread_id, result["history"])
                    for event in result["events"]:
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                else:
                    async for mode, payload in app.state.graph.astream(result, stream_mode=["updates", "custom"]):
                        if mode == "custom":
                            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                            continue
                        for node_name, delta in payload.items():
                            result.update(delta)
                            for event in (delta.get("events") or [])[seen:]:
                                yield f"data: {json.dumps({**event, 'node': node_name}, ensure_ascii=False)}\n\n"
                            if delta.get("events"):
                                seen = len(delta["events"])
        except asyncio.CancelledError:
            _log_canceled(result, run_id)
            raise
        except Exception as error:
            logging.warning("stream request failed: %s", type(error).__name__)
            result = failure_result(result, error)
            yield f"data: {json.dumps(result['events'][-1], ensure_ascii=False)}\n\n"
        # Cancellation is a BaseException and propagates above: interrupted work is not saved as completed.
        _save_result(deps, message, thread_id, result, run_id)
        final_event = {"type": "FinalAnswer", "run_id": run_id, "reply": result["final"], "prompt": result.get("last_prompt"),
                       "processing_status": result.get("processing_status", "completed"),
                       "validation_status": result.get("validation_status"), "report": result.get("report"),
                       "comparison": result.get("comparison"), "comparison_text": render_comparison(result.get("comparison", []))}
        yield f"data: {json.dumps(final_event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")
