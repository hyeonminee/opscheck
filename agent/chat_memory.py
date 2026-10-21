"""명확한 이름 소개·재질문만 사용자 발언에서 확인한다."""
import re

_INTRO = re.compile(r"(?:제|내)\s*이름은\s*(\S+?)입니다[.!]?")
_QUESTION = re.compile(r"(?:제|내)\s*이름이\s*뭐(?:였죠|였지|죠|야)?\??")


def name_reply(query: str, history: list[dict]) -> str | None:
    intro = _INTRO.fullmatch(query.strip())
    if intro:
        return f"{intro.group(1)}님, 반갑습니다. 무엇을 도와드릴까요?"
    if not _QUESTION.fullmatch(query.strip()):
        return None
    for message in reversed(history[-10:]):
        if message.get("role") != "user":
            continue
        intro = _INTRO.fullmatch(message.get("content", "").strip())
        if intro:
            return f"{intro.group(1)}님이라고 말씀하셨어요."
    return "최근 대화에서 이름을 확인하지 못했어요. 이름을 알려주시겠어요?"
