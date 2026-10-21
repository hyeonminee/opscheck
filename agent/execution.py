"""실행 한도와 실패 시 확인한 관측만 반환하는 공통 정책."""
import httpx

REQUEST_TIMEOUT = 120.0
MCP_TIMEOUT = 10.0
MCP_ATTEMPTS = 2
MODEL_TIMEOUT = 45.0
FAST_TOKENS = 512
ANSWER_TOKENS = 1024
MAX_DRAFTS = 2


class GenerationLimitError(RuntimeError):
    """잘렸거나 끝까지 수신하지 못한 응답은 완성된 답변이 아니다."""


def error_event(error):
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        code, message = 'timeout', '처리 시간 제한에 도달했습니다. 확인된 자료만 안내합니다.'
    elif isinstance(error, GenerationLimitError):
        code, message = 'generation_limit', '답변이 생성 한도에 도달했거나 중단됐습니다. 초안 대신 확인된 자료만 안내합니다.'
    else:
        code, message = 'processing_error', '요청을 완료하지 못했습니다. 확인된 자료와 미확인 사항을 구분해 안내합니다.'
    return {'type': 'ProcessingError', 'code': code, 'message': message}


def observation_reply(collected, reason):
    from .report import build_report, render_report
    return render_report(build_report(collected, [], 'unverified', reason=reason))


def failure_result(state, error):
    event = error_event(error)
    from .report import build_report, render_report
    report = build_report(state.get('collected', []), state.get('requested_targets', []), 'unverified', reason=event['message'])
    return {**state, 'final': render_report(report), 'report': report,
            'last_prompt': None, 'processing_status': 'failed', 'validation_status': 'unverified',
            'events': state.get('events', []) + [event]}
