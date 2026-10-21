"""읽기 전용 MCP 호출의 일시 오류만 제한된 횟수·시간 안에서 재시도한다."""
import asyncio
import httpx
from langsmith import traceable
from .execution import MCP_TIMEOUT, MCP_ATTEMPTS


@traceable(name='with_retry')
async def with_retry(fn, *args, retries=MCP_ATTEMPTS, base_delay=.3, attempt_timeout=None, on_attempt=None, **kwargs):
    for attempt in range(retries):
        if on_attempt:
            on_attempt(attempt, 'running')
        try:
            async with asyncio.timeout(MCP_TIMEOUT if attempt_timeout is None else attempt_timeout):
                return await fn(*args, **kwargs), attempt
        except (TimeoutError, ConnectionError, httpx.TransportError) as error:
            if on_attempt:
                on_attempt(attempt, 'failed')
            if attempt + 1 >= retries:
                error.retry_count = attempt
                raise
            await asyncio.sleep(base_delay * (3 ** attempt))
        except Exception:
            if on_attempt:
                on_attempt(attempt, 'failed')
            raise
