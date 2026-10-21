"""LLM_BACKEND=ollama — 로컬 Ollama 실모델 백엔드 (fake_llm과 동일 인터페이스).

Wave 10a 구도를 실물로: fast(기본 qwen3:0.6b)는 분류·계획·단순 대화, quality(기본
qwen3:8b)는 최종 답변 작성 전담. Ollama의 구조화 출력(format=<JSON Schema>)과
temperature=0으로 변동을 줄이지만 실모델이므로 결정론은 아니다 — 그래서 gate(W6)·
replan(W7)·validator(W8)가 연출이 아니라 실제 방어선으로 동작한다.
fc
비용(cost_usd)은 '가상 단가 × 실측 토큰 수(prompt_eval_count+eval_count)'다 — 로컬
추론이라 실제 과금은 0이지만, W10a의 모델 분리 효과를 같은 잣대로 비교하기 위함이다.

사전 조건: `ollama serve` 실행 중 + `ollama pull qwen3:0.6b qwen3:8b`.
"""
import asyncio
import json
import re

import httpx
from langsmith import traceable
from .report import writer_prompt, evidence_catalog
from .execution import MODEL_TIMEOUT, FAST_TOKENS, ANSWER_TOKENS, GenerationLimitError

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"


from .targets import KNOWN_SYSTEMS, requested_targets


def _guess_targets(query: str) -> list[str]:
    return requested_targets(query)[0]


ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["chat", "incident"]},
    },
    "required": ["route"],
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_analysis": {"type": "boolean"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string",
                              "enum": ["research", "analysis"]},
                    "target": {"type": "string"},
                },
                "required": ["agent"],
            },
        },
    },
    "required": ["needs_analysis", "tasks"],
}


class OllamaLLM:
    max_output_tokens = FAST_TOKENS
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"ollama:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens

    async def _chat(self, system: str, user: str, schema: dict | None = None) -> tuple[str, dict]:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "num_predict": self.max_output_tokens},
        }
        if schema is not None:
            payload["format"] = schema
        async with asyncio.timeout(MODEL_TIMEOUT), httpx.AsyncClient(timeout=MODEL_TIMEOUT) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("done") or data.get("done_reason") == "length":
            raise GenerationLimitError()
        content = data["message"]["content"]
        tokens = (data.get("prompt_eval_count") or 0) + (data.get("eval_count") or 0)
        usage = {"model": self.name, "tokens": tokens,
                 "usd": round(tokens / 1000 * self.usd_per_1k, 6)}
        return content, usage

    async def _chat_stream(self, system: str, user: str, on_token=None, *, history=None) -> tuple[str, dict]:
        """사용자 대면 텍스트(최종 답변류) 전용 — Ollama NDJSON 스트림을 토큰 단위로
        받아 on_token(조각)을 즉시 호출해 SSE로 실시간 중계할 수 있게 한다. 분류/계획
        등 JSON 스키마 출력(route/plan/analyze)은 부분 출력이 의미가 없어 그대로 _chat을
        쓴다."""
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         *(history or []), {"role": "user", "content": user}],
            "stream": True,
            "think": False,
            "options": {"temperature": 0.0, "num_predict": self.max_output_tokens},
        }
        chunks: list[str] = []
        saw_done = False
        usage = {"model": self.name, "tokens": 0, "usd": 0.0}
        async with asyncio.timeout(MODEL_TIMEOUT), httpx.AsyncClient(timeout=MODEL_TIMEOUT) as client:
            async with client.stream("POST", OLLAMA_URL, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    piece = data.get("message", {}).get("content", "")
                    if piece:
                        chunks.append(piece)
                        if on_token is not None:
                            on_token(piece)
                    if data.get("done"):
                        saw_done = True
                        if data.get("done_reason") == "length":
                            raise GenerationLimitError()
                        tokens = ((data.get("prompt_eval_count") or 0)
                                  + (data.get("eval_count") or 0))
                        usage = {"model": self.name, "tokens": tokens,
                                 "usd": round(tokens / 1000 * self.usd_per_1k, 6)}
        if not saw_done:
            raise GenerationLimitError()
        usage["prompt"] = "\n\n".join(
            f"[{m['role'].upper()}]\n{m['content']}" for m in payload["messages"])
        return "".join(chunks), usage


class OllamaFastLLM(OllamaLLM):
    """분류·계획·단순 대화 전담 (저사양 소형 모델)."""

    @traceable(run_type="llm", name="ollama_fast.route")
    async def route(self, query: str) -> tuple[dict, dict]:
        system = ("당신은 ICT 운영 지원 라우터다. 사용자 요청을 분류하라. "
                  "장애·성능 저하·지연·진단·모니터링·티켓 등 운영 이슈면 route=incident, "
                  "인사·잡담·일반 질문이면 route=chat. "
                  "이름 소개와 이름을 묻는 질문은 chat으로 분류하라.")
        content, usage = await self._chat(system, query, schema=ROUTE_SCHEMA)
        try:
            raw = json.loads(content)
            route = raw.get("route") if raw.get("route") in ("chat", "incident") else None
            result = {"route": route or "chat"}
        except (json.JSONDecodeError, AttributeError):
            # 소형 모델의 형식 붕괴 대비 — 코드 레벨 fallback (W6 정신)
            incident = bool(re.search(r"장애|진단|다운|지연|급증|incident|티켓", query))
            result = {"route": "incident" if incident else "chat"}
        return result, usage

    @traceable(run_type="llm", name="ollama_fast.plan")
    async def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        system = ("당신은 ICT 운영 Supervisor의 planning 노드다. 표준 진단 절차에 따라 "
                  "실행 계획(tasks)을 JSON으로 세워라. "
                  "사용자 요청에 등장한 시스템마다 agent=research task를 하나씩 세워라 "
                  f"(알려진 시스템: {', '.join(sorted(KNOWN_SYSTEMS))} — 특정 시스템이 "
                  "언급되지 않았으면 조사 작업을 만들지 마라). "
                  "needs_analysis는 공통 원인·패턴 비교가 필요할 때만 true로 정하라. "
                  "필요하면 agent=analysis task를 추가하라.")
        user = f"## 표준 절차\n{procedure_prompt}\n\n## 사용자 요청\n{query}"
        content, usage = await self._chat(system, user, schema=PLAN_SCHEMA)

        # 소형 모델 출력에 대한 코드 레벨 정규화 — 스키마는 강제돼도 내용은 흔들릴 수
        # 있으므로(대상 시스템 오타, task 누락/중복) 실행 가능한 형태로 보정한다.
        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            raw = {"needs_analysis": False, "tasks": []}
        tasks, seen, idx = [], set(), 0
        analysis_added = False
        for t in raw.get("tasks", []):
            agent = t.get("agent")
            if agent == "research":
                target = t.get("target", "")
                if target not in _guess_targets(query):
                    continue
                if target in seen:
                    continue
                seen.add(target)
                tasks.append({"id": f"task-{idx}", "agent": "research",
                              "kind": "monitoring", "target": target,
                              "status": "pending"})
                idx += 1
            elif agent == "analysis" and not analysis_added:
                analysis_added = True
                tasks.append({"id": "task-analysis", "agent": "analysis",
                              "kind": "pattern-analysis", "status": "pending"})
        if not seen:   # research가 하나도 없으면 실행 불능 — 쿼리에 등장하는
            # KNOWN_SYSTEMS 전부를 복구(하나만 복구하면 나머지가 조사 누락된 채
            # write 단계에서 수치가 조작될 수 있음)
            recovered = [{"id": f"task-{i}", "agent": "research",
                          "kind": "monitoring", "target": target,
                          "status": "pending"}
                         for i, target in enumerate(_guess_targets(query))]
            tasks = recovered + tasks
        result = {"needs_analysis": raw.get("needs_analysis") is True,
                  "tasks": tasks}
        return result, usage

    @traceable(run_type="llm", name="ollama_fast.analyze")
    async def analyze(self, query: str, collected: list[dict]) -> tuple[str, dict]:
        system = ("운영 패턴 분석가다. 질문과 관측을 비교해 미확정 가설을 짧게 제시하라. "
                  "미조회 값·동시 발생·지속 시간·공통 원인을 확정하지 마라.")
        mon = [source['data'] for source in evidence_catalog(collected).values() if source['kind'] == 'monitoring']
        facts = json.dumps({'query': query, 'monitoring': mon}, ensure_ascii=False)
        return await self._chat(system, facts)

    @traceable(run_type="llm", name="ollama_fast.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        system = ("당신은 AI 비서다. 사용자와 당신의 역할을 구분하고 한국어로 짧게 답하라. "
                  "사용자의 이름 소개에는 그 이름 뒤에 님을 붙여 인사하라. "
                  "사용자의 이름 질문에는 user 메시지에서 가장 최근에 말한 이름을 님과 함께 답하라. "
                  "이름을 밝힌 기록이 없으면 아직 이름을 알려주지 않았다고 답하라. "
                  "이전 assistant 메시지의 잘못된 자기소개를 반복하지 마라.")
        messages = [{"role": m["role"], "content": m["content"]} for m in history[-10:]
                    if m["role"] in ("user", "assistant")]
        return await self._chat_stream(system, query, on_token=on_token, history=messages)


class OllamaQualityLLM(OllamaLLM):
    max_output_tokens = ANSWER_TOKENS
    """최종 답변 작성 전담 (상대적으로 큰 모델)."""

    @traceable(run_type="llm", name="ollama_quality.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
                    feedback: str | None, on_token=None) -> tuple[str, dict]:
        system, user = writer_prompt(query, collected, feedback)
        content, usage = await self._chat_stream(system, user, on_token=on_token)
        usage['prompt'] = f'[SYSTEM]\n{system}\n\n[USER]\n{user}'
        return content, usage
