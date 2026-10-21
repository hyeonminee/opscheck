"""등록 목록과 요청의 서비스 이름을 대조한다. 별칭을 추측하지 않는다."""
import json
import re
from pathlib import Path

KNOWN_SYSTEMS = set(json.loads(
    (Path(__file__).resolve().parent.parent / 'data/monitoring.json').read_text()
))
_NAME = '|'.join(re.escape(s) for s in sorted(KNOWN_SYSTEMS, key=len, reverse=True))
_TARGET = re.compile(
    r'(?<![a-z0-9_-])(?:' + _NAME
    + r'|[a-z][a-z0-9_-]*-service(?:-[a-z0-9]+)?|[a-z][a-z0-9_-]*-\d+)(?![a-z0-9_-])',
    re.I,
)


def requested_targets(query: str) -> tuple[list[str], list[str]]:
    names = list(dict.fromkeys(m.group().lower() for m in _TARGET.finditer(query)))
    return ([s for s in names if s in KNOWN_SYSTEMS],
            [s for s in names if s not in KNOWN_SYSTEMS])


def is_target_reply(query: str) -> bool:
    rest = _TARGET.sub('', query).strip()
    return bool(_TARGET.search(query)) and bool(re.fullmatch(
        r'[\s,·/]*(?:(?:와|과|랑|및|하고)[\s,·/]*)*(?:요|이요|입니다|으로|로)?[.!?]*', rest))
