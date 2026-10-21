"""LLM_BACKEND=openai — 실제 OpenAI API 백엔드 (fake_llm/ollama_llm과 동일 인터페이스).

Wave 10a 구도를 실제 상용 API로: fast(기본 gpt-4o-mini)는 분류·계획·단순 대화,
quality(기본 gpt-4o)는 최종 답변 작성 전담. ollama_llm.py와 마찬가지로 실모델이라
결정론은 아니다 — gate(W6)·replan(W7)·validator(W8)가 연출이 아니라 실제 방어선으로
동작한다. Ollama와 달리 실제 과금이 발생하는 원격 API이므로, 이 백엔드로 실행하면
OpenAI 계정에 실제 비용이 청구된다(run_openai.sh 실행 전 반드시 인지할 것).

구조화 출력은 OpenAI Chat Completions의 `response_format={"type": "json_object"}`
(JSON 모드)로 강제한다 — Ollama의 `format=<JSON Schema>`처럼 스키마 자체를 강제하는
기능은 아니라서, 시스템 프롬프트에 원하는 필드를 명시하고 ollama_llm.py와 동일하게
코드 레벨 방어(파싱 실패·필드 누락 시 보정)를 그대로 둔다.

비용(cost_usd)은 OpenAI 응답의 `usage.total_tokens`(실측) × 가상 단가다 — 실제 청구
금액과는 별개로, W10a의 모델 분리 효과를 fake/ollama와 같은 잣대로 비교하기 위함이다.
(OpenAI는 입력/출력 토큰 단가가 실제로는 다르지만, 세 백엔드의 인터페이스를
`usd_per_1k_tokens` 하나로 통일해두기 위해 여기서도 단일 블렌디드 단가로 단순화한다.)

사전 조건: 유효한 `OPENAI_API_KEY` 환경변수(.env).
"""
import asyncio
import json
import os
import re

import httpx
from langsmith import traceable
from .report import writer_prompt, evidence_catalog
from .execution import MODEL_TIMEOUT, FAST_TOKENS, ANSWER_TOKENS, GenerationLimitError

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


from .targets import KNOWN_SYSTEMS, requested_targets


def _guess_targets(query: str) -> list[str]:
    return requested_targets(query)[0]


ROUTE_INSTRUCTIONS = (
    'JSON 객체로만 응답하라. 스키마: '
    '{"route": "chat" 또는 "incident"}'
)

PLAN_INSTRUCTIONS = (
    'JSON 객체로만 응답하라. 스키마: {"needs_analysis": boolean, '
    '"tasks": [{"agent": "research"|"analysis", "target": string(선택)}]}'
)


class OpenAILLM:
    max_output_tokens = FAST_TOKENS
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"openai:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens
        self.api_key = os.environ.get("OPENAI_API_KEY", "")

    async def _chat(self, system: str, user: str, json_mode: bool = False, *, history=None, on_token=None) -> tuple[str, dict]:
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY가 설정되지 않았습니다 — .env 또는 환경변수로 지정하세요."
            )
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         *(history or []), {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": self.max_output_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        async with asyncio.timeout(MODEL_TIMEOUT), httpx.AsyncClient(timeout=MODEL_TIMEOUT) as client:
            if on_token is None:
                resp = await client.post(OPENAI_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            else:
                payload.update(stream=True, stream_options={"include_usage": True})
                pieces, finish_reason, done, tokens = [], None, False, 0
                async with client.stream("POST", OPENAI_URL, json=payload, headers=headers) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith('data:'):
                            continue
                        value = line[5:].strip()
                        if value == '[DONE]':
                            done = True
                            break
                        chunk = json.loads(value)
                        if chunk.get('error'):
                            raise RuntimeError('모델 응답 스트림에 오류가 발생했습니다.')
                        if chunk.get('usage'):
                            tokens = chunk['usage'].get('total_tokens', 0)
                        for choice in chunk.get('choices', []):
                            piece = choice.get('delta', {}).get('content')
                            if piece:
                                pieces.append(piece)
                                on_token(piece)
                            if choice.get('finish_reason'):
                                finish_reason = choice['finish_reason']
                if not done or finish_reason != 'stop':
                    raise GenerationLimitError()
                return ''.join(pieces), {'model': self.name, 'tokens': tokens,
                    'usd': round(tokens / 1000 * self.usd_per_1k, 6)}
        if data["choices"][0].get("finish_reason") != "stop":
            raise GenerationLimitError()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)
        usage = {"model": self.name, "tokens": tokens,
                 "usd": round(tokens / 1000 * self.usd_per_1k, 6)}
        return content, usage


class OpenAIFastLLM(OpenAILLM):
    """분류·계획·단순 대화 전담 (저비용 모델, 기본 gpt-4o-mini)."""

    @traceable(run_type="llm", name="openai_fast.route")
    async def route(self, query: str) -> tuple[dict, dict]:
        system = ("당신은 ICT 운영 지원 라우터다. 사용자 요청을 분류하라. "
                  "장애·성능 저하·지연·진단·모니터링·티켓 등 운영 이슈면 route=incident, "
                  "인사·잡담·일반 질문이면 route=chat. "
                  "이름 소개와 이름을 묻는 질문은 chat으로 분류하라. "
                  + ROUTE_INSTRUCTIONS)
        content, usage = await self._chat(system, query, json_mode=True)
        try:
            raw = json.loads(content)
            route = raw.get("route") if raw.get("route") in ("chat", "incident") else None
            result = {"route": route or "chat"}
        except (json.JSONDecodeError, AttributeError):
            # JSON 모드는 "유효한 JSON"만 보장할 뿐 스키마까지 강제하진 않으므로,
            # 필드 누락·오탐 시 코드 레벨 fallback을 둔다(W6 정신, ollama_llm.py와 동일).
            incident = bool(re.search(r"장애|진단|다운|지연|급증|incident|티켓", query))
            result = {"route": "incident" if incident else "chat"}
        return result, usage

    @traceable(run_type="llm", name="openai_fast.plan")
    async def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        system = ("당신은 ICT 운영 Supervisor의 planning 노드다. 표준 진단 절차에 따라 "
                  "실행 계획(tasks)을 JSON으로 세워라. "
                  "사용자 요청에 등장한 시스템마다 agent=research task를 하나씩 세워라 "
                  f"(알려진 시스템: {', '.join(sorted(KNOWN_SYSTEMS))} — 특정 시스템이 "
                  "언급되지 않았으면 조사 작업을 만들지 마라). "
                  "needs_analysis는 공통 원인·패턴 비교가 필요할 때만 true로 정하라. "
                  "필요하면 agent=analysis task를 추가하라. "
                  + PLAN_INSTRUCTIONS)
        user = f"## 표준 절차\n{procedure_prompt}\n\n## 사용자 요청\n{query}"
        content, usage = await self._chat(system, user, json_mode=True)

        # 코드 레벨 정규화 — JSON 형식은 지켜져도 내용은 흔들릴 수 있으므로
        # (대상 시스템 오타, task 누락/중복) 실행 가능한 형태로 보정한다.
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

    @traceable(run_type="llm", name="openai_fast.analyze")
    async def analyze(self, query: str, collected: list[dict]) -> tuple[str, dict]:
        system = ("운영 패턴 분석가다. 질문과 관측을 비교해 미확정 가설을 짧게 제시하라. "
                  "미조회 값·동시 발생·지속 시간·공통 원인을 확정하지 마라.")
        mon = [source['data'] for source in evidence_catalog(collected).values() if source['kind'] == 'monitoring']
        facts = json.dumps({'query': query, 'monitoring': mon}, ensure_ascii=False)
        return await self._chat(system, facts)

    @traceable(run_type="llm", name="openai_fast.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        """콜백이 있으면 생성 조각을 즉시 전달하고, 없으면 완성 응답을 반환한다."""
        system = ("당신은 AI 비서다. 사용자와 당신의 역할을 구분하고 한국어로 짧게 답하라. "
                  "사용자의 이름 소개에는 그 이름 뒤에 님을 붙여 인사하라. "
                  "사용자의 이름 질문에는 user 메시지에서 가장 최근에 말한 이름을 님과 함께 답하라. "
                  "이름을 밝힌 기록이 없으면 아직 이름을 알려주지 않았다고 답하라. "
                  "이전 assistant 메시지의 잘못된 자기소개를 반복하지 마라.")
        messages = [{"role": m["role"], "content": m["content"]} for m in history[-10:]
                    if m["role"] in ("user", "assistant")]
        content, usage = await self._chat(system, query, history=messages, on_token=on_token)
        usage["prompt"] = "\n\n".join(
            f"[{m['role'].upper()}]\n{m['content']}" for m in
            [{"role": "system", "content": system}, *messages, {"role": "user", "content": query}])
        return content, usage


class OpenAIQualityLLM(OpenAILLM):
    max_output_tokens = ANSWER_TOKENS
    """최종 답변 작성 전담 (고품질 모델, 기본 gpt-4o)."""

    @traceable(run_type="llm", name="openai_quality.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
                    feedback: str | None, on_token=None) -> tuple[str, dict]:
        system, user = writer_prompt(query, collected, feedback)
        content, usage = await self._chat(system, user, json_mode=True, on_token=on_token)
        usage['prompt'] = f'[SYSTEM]\n{system}\n\n[USER]\n{user}'
        return content, usage
