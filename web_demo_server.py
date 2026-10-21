"""web_demo_server.py — all_total_vue 에이전트와 브라우저 채팅창으로 대화해보는 데모.

web_chat.html이 바닐라 JS 대신 Vue 3(CDN 글로벌 빌드, 별도 빌드 단계 없음)로 되어
있다는 점만 all_total과 다르다. server.py(8100, 기존 통합 테스트 서버)는 전혀
건드리지 않는다. 이 스크립트는
- web_chat.html 한 장을 같은 출처(origin)로 띄우고
- /chat/stream 요청을 그대로 8100으로 중계(프록시)해서

브라우저의 CORS 제약 없이 실제 에이전트와 대화할 수 있게 해주는 아주 얇은 레이어다.
server.py는 평소처럼(fake/ollama/openai 아무 백엔드로나) 8100에 미리 띄워 놓아야 한다.

실행:
    python3 web_demo_server.py        # http://localhost:8600 에서 채팅 화면 오픈
"""
import asyncio
import json
from pathlib import Path
from urllib.parse import quote

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from agent.execution import REQUEST_TIMEOUT

BACKEND = "http://127.0.0.1:8100"
HERE = Path(__file__).parent

app = FastAPI()


@app.get("/")
async def index():
    return FileResponse(HERE / "web_chat.html")


@app.get("/health")
async def proxy_health():
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{BACKEND}/health")
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/sessions/{thread_id}/messages")
async def proxy_messages(thread_id: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{BACKEND}/sessions/{quote(thread_id, safe='')}/messages")
    return JSONResponse(r.json(), status_code=r.status_code)


async def _history_response(path, thread_id, limit=None):
    params = {'thread_id': thread_id}
    if limit is not None:
        params['limit'] = limit
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(BACKEND + path, params=params)
        return JSONResponse(response.json(), status_code=response.status_code)
    except (httpx.HTTPError, ValueError):
        return JSONResponse({'detail': '조사 이력을 불러오지 못했습니다. 서버 연결을 확인해 주세요.'}, status_code=502)


@app.get('/investigations')
async def proxy_investigations(thread_id: str, limit: int = 20):
    return await _history_response('/investigations', thread_id, limit)


@app.get('/investigations/{run_id}')
async def proxy_investigation(run_id: str, thread_id: str):
    return await _history_response('/investigations/' + quote(run_id, safe=''), thread_id)


@app.get('/investigations/{run_id}/report.md')
async def proxy_markdown(run_id: str, thread_id: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(BACKEND + '/investigations/' + quote(run_id, safe='') + '/report.md', params={'thread_id': thread_id})
        headers = {key: response.headers[key] for key in ('content-type','content-disposition') if key in response.headers}
        return Response(response.content, status_code=response.status_code, headers=headers)
    except httpx.HTTPError:
        return JSONResponse({'detail': '보고서 다운로드에 실패했습니다. 다시 시도해 주세요.'}, status_code=502)


@app.post("/chat")
async def proxy_chat(req: Request):
    body = await req.body()
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT + 10) as client:
            r = await client.post(f"{BACKEND}/chat", content=body, headers={"content-type": "application/json"})
        return JSONResponse(r.json(), status_code=r.status_code)
    except (httpx.HTTPError, ValueError):
        return JSONResponse({"reply": "서버 응답을 완료하지 못했습니다. 잠시 후 다시 질문해 주세요.",
                             "events": [{"type": "ProcessingError", "code": "proxy_error", "message": "서버 연결 실패"}],
                             "metadata": {"processing_status": "failed"}}, status_code=502)



@app.get("/chat/stream")
async def proxy_stream(message: str, thread_id: str = "web-demo"):
    async def relay():
        completed = False
        tail = b""
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT + 10):
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("GET", f"{BACKEND}/chat/stream",
                                             params={"message": message, "thread_id": thread_id}) as upstream:
                        upstream.raise_for_status()
                        async for chunk in upstream.aiter_bytes():
                            tail = (tail + chunk)[-64:]
                            completed = completed or b"data: [DONE]" in tail
                            yield chunk
        except (httpx.HTTPError, TimeoutError):
            pass
        if not completed:
            event = {"type": "ProcessingError", "code": "proxy_error",
                     "message": "서버 연결이 끊겼거나 응답을 완료하지 못했습니다. 다시 질문해 주세요."}
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
            yield b"data: [DONE]\n\n"

    return StreamingResponse(relay(), media_type="text/event-stream")


if __name__ == "__main__":
    print("브라우저에서 http://localhost:8600 을 열어보세요 (server.py가 8100에 떠 있어야 합니다)")
    uvicorn.run(app, host="0.0.0.0", port=8600)
