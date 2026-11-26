"""Роутер моделей: основная → запасная."""

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence

from app.assistant.llm.base import LLMError, LLMEvent, LLMProvider, LLMRequest, ModelStarted, Usage

logger = logging.getLogger(__name__)

ALL_FAILED = "Ассистент сейчас недоступен: ни основная, ни запасная модель не ответила. Попробуйте позже."
BROKEN_MIDWAY = "Связь с моделью оборвалась посреди ответа. Попробуйте ещё раз."


class ModelRouter:
    """Перебирает модели по порядку. На запасную переходит при 429, 5xx, таймауте и обрыве связи —
    и только пока модель ничего не успела выдать: ответ не склеивается из двух моделей.
    Ошибки из-за самого запроса (прочие 4xx) не повторяются.

    call_timeout — потолок на весь вызов, first_event_timeout — сколько ждать первого события модели.
    Второй действует, только пока есть на кого переключиться."""

    def __init__(
        self,
        providers: Sequence[LLMProvider],
        *,
        call_timeout: float,
        first_event_timeout: float | None = None,
    ):
        self._providers = providers
        self._call_timeout = call_timeout
        self._first_event_timeout = first_event_timeout

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        for index, provider in enumerate(self._providers):
            started = False
            has_spare = index < len(self._providers) - 1
            try:
                async for event in self._with_deadline(provider, request, has_spare):
                    if not started and not isinstance(event, Usage):
                        started = True
                        yield ModelStarted(provider.model, fallback=index > 0)
                    yield event
                return
            except LLMError as exc:
                # В лог — только код ошибки: текст запроса может содержать данные пользователя.
                logger.warning("LLM %s: %s (код %s %s)", provider.model, exc.reason, exc.status, exc.detail)
                if not exc.retryable:
                    raise
                if started:
                    raise LLMError(BROKEN_MIDWAY, retryable=False, status=exc.status) from exc
        raise LLMError(ALL_FAILED, retryable=False)

    async def _with_deadline(
        self, provider: LLMProvider, request: LLMRequest, has_spare: bool
    ) -> AsyncIterator[LLMEvent]:
        """Потолки времени на один вызов модели (сверх сетевых таймаутов клиента)."""
        loop = asyncio.get_running_loop()
        final = loop.time() + self._call_timeout
        deadline = final
        if has_spare and self._first_event_timeout is not None:
            deadline = min(final, loop.time() + self._first_event_timeout)
        stream = provider.stream(request)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(anext(stream), timeout=deadline - loop.time())
                except StopAsyncIteration:
                    return
                except TimeoutError as exc:
                    raise LLMError("Модель не ответила вовремя.", retryable=True) from exc
                deadline = final
                yield event
        finally:
            await stream.aclose()
