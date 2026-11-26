"""Сборка ассистента из настроек. Чтобы добавить провайдера, достаточно добавить его в список моделей."""

from app.assistant.agent import AssistantDeps
from app.assistant.llm.base import LLMError, LLMProvider, LLMRequest
from app.assistant.llm.openai_provider import OpenAIContainers, OpenAIProvider, make_client
from app.assistant.llm.router import ModelRouter
from app.assistant.llm.stub import StubContainers, StubProvider, demo_script
from app.config import Settings
from app.db import SessionFactory
from app.services.fx import FxClient


class NotConfiguredProvider:
    model = "none"

    async def stream(self, request: LLMRequest):
        raise LLMError("Ассистент не настроен: не задан ключ OPENAI_API_KEY.", retryable=False)
        yield  # делает функцию асинхронным генератором


def build(settings: Settings, fx: FxClient) -> AssistantDeps:
    providers: list[LLMProvider]
    if settings.llm_stub:
        providers, containers = [StubProvider(demo_script)], StubContainers()
    elif not settings.openai_api_key:
        providers, containers = [NotConfiguredProvider()], StubContainers()
    else:
        client = make_client(settings.openai_api_key)
        models = [settings.llm_primary_model, settings.llm_fallback_model]
        providers = [
            OpenAIProvider(client, m, settings.llm_service_tier, settings.llm_reasoning_effort)
            for m in models
        ]
        containers = OpenAIContainers(client)
    router = ModelRouter(
        providers,
        call_timeout=settings.llm_call_timeout_seconds,
        first_event_timeout=settings.llm_first_event_timeout_seconds,
    )
    return AssistantDeps(router=router, containers=containers, sessions=SessionFactory, fx=fx)
