"""Wave 4/10a — 결정론적 Fake LLM 2종 (fast/quality) + 실측 비용 계산.

실제 LLM 호출 없이도 파이프라인 전체가 결정론적으로 재현되도록, 프롬프트 내용에 따라
정해진 응답을 돌려준다. 각 호출은 LangSmith에 run_type="llm"으로 기록되고, 문자열
길이 기반 토큰 추정치로 비용을 계산해 상태에 누적한다 (Wave 10a의 모델 분리 근거).
"""
import json
import re

from langsmith import traceable
from .report import evidence_catalog

from .targets import requested_targets
from .chat_memory import name_reply


class FakeLLM:
    def __init__(self, name: str, usd_per_1k_tokens: float):
        self.name = name
        self.usd_per_1k = usd_per_1k_tokens

    def _usage(self, prompt: str, completion: str) -> dict:
        tokens = (len(prompt) + len(completion)) // 4 + 1
        return {"model": self.name, "tokens": tokens,
                "usd": round(tokens / 1000 * self.usd_per_1k, 6)}


class FastLLM(FakeLLM):
    """분류·계획 등 짧고 정형적인 단계 전담 (저비용)."""

    @traceable(run_type="llm", name="fast_llm.route")
    async def route(self, query: str) -> tuple[dict, dict]:
        """Wave 5 — 쿼리 라우팅. 장애 관련이면 incident 파이프라인, 아니면 chat 직행."""
        incident = bool(re.search(r"장애|진단|다운|지연|급증|incident|티켓", query))
        result = {"route": "incident" if incident else "chat"}
        return result, self._usage(query, str(result))

    @traceable(run_type="llm", name="fast_llm.plan")
    async def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        """Wave 4 — Plan/Task 분해. MCP Prompt가 준 표준 절차를 따라 계획을 세운다.

        요청한 등록 시스템만 조사하고, 공통 원인·패턴 비교의 분석 여부를 결정한다.
        """
        systems = requested_targets(query)[0]
        tasks = [
            {"id": f"task-{i}", "agent": "research", "kind": "monitoring",
             "target": s, "status": "pending"}
            for i, s in enumerate(dict.fromkeys(systems))
        ]
        needs_analysis = bool(re.search(r"공통|패턴|비교", query))
        result = {"needs_analysis": needs_analysis, "tasks": tasks}
        return result, self._usage(query + procedure_prompt, str(result))

    @traceable(run_type="llm", name="fast_llm.analyze")
    async def analyze(self, query: str, collected: list[dict]) -> tuple[str, dict]:
        mon = [c['data'] for c in collected if c.get('source') == 'monitoring']
        summary = '조회한 관측만으로 공통 원인·지속 여부를 확정할 수 없습니다.'
        return summary, self._usage(query + json.dumps(mon, ensure_ascii=False), summary)

    @traceable(run_type="llm", name="fast_llm.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        """Wave 5의 direct 경로 + Wave 12 증명용 — 히스토리에서 이름을 기억해낸다.

        fake 백엔드는 실제 네트워크 호출이 없어 토큰 단위로 쪼갤 대상이 없다 —
        ollama_llm.py와 인터페이스(on_token 콜백)만 맞추고 결과를 한 번에 반환한다."""
        reply = name_reply(query, history) or "안녕하세요! 무엇을 도와드릴까요?"
        if on_token is not None:
            on_token(reply)
        return reply, self._usage(query, reply)


class QualityLLM(FakeLLM):
    """최종 답변 작성 전담 (고품질·고비용)."""

    @traceable(run_type="llm", name="quality_llm.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
                    feedback: str | None, on_token=None) -> tuple[str, dict]:
        # Keep the classroom rejection/rewrite demonstration deterministic.
        if attempt == 1:
            draft = '재시작하면 됩니다.'
        else:
            sources = evidence_catalog(collected)
            analysis, actions = [], []
            for source in sources.values():
                if source['kind'] != 'monitoring' or not source['usable']:
                    continue
                analysis.append({'system_name': source['system_name'],
                    'hypothesis': '관측된 지표와 증상의 관련성을 추가 확인할 필요가 있습니다.',
                    'evidence_ids': [source['id']], 'claims': [
                        {'evidence_id': source['id'], 'field': field, 'value': source['data'][field]}
                        for field in ('status', 'cpu_percent', 'memory_percent') if source['data'].get(field) is not None]})
            for document in sources.values():
                if document['kind'] != 'runbook' or not document['steps']:
                    continue
                for system in document['related_systems']:
                    if not sources.get('obs:' + system, {}).get('usable'):
                        continue
                    actions.append({'system_name': system, 'document_id': document['id'],
                        'step_index': min(document['steps']), 'evidence_ids': ['obs:' + system, document['id']],
                        'condition_verified': False})
            draft = json.dumps({'analysis': analysis, 'actions': actions}, ensure_ascii=False)
        if on_token is not None:
            on_token(draft)
        return draft, self._usage(query + str(feedback), draft)
