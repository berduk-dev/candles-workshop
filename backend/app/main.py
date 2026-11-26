import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import audit, auth, chat, inventory, production
from app.assistant import factory
from app.assistant.chat import mark_stale_as_interrupted
from app.config import get_settings
from app.db import SessionFactory
from app.errors import DomainError
from app.services.fx import FxClient
from app.validation_messages import humanize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "openai", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.fx = FxClient(settings.fx_base_url, timeout=settings.fx_timeout_seconds)
    app.state.assistant = factory.build(settings, app.state.fx)
    await mark_stale_as_interrupted(SessionFactory)
    yield
    await app.state.fx.aclose()


app = FastAPI(
    title="Учёт производства свечей",
    description="Склад, производство и ИИ-ассистент мастерской ароматических свечей.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Свой журнал запросов вместо uvicorn: без строки параметров — в ней бывают имена (?supplier=…)."""
    started = time.monotonic()
    response = await call_next(request)
    ms = int((time.monotonic() - started) * 1000)
    if request.url.path != "/api/health":
        logger.info("%s %s → %s, %s мс", request.method, request.url.path, response.status_code, ms)
    return response


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": humanize(exc.errors()), "code": "invalid_input"})


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # Подробности — только в лог; пользователю — понятный текст и код для поиска в логах.
    error_id = uuid.uuid4().hex[:8]
    logger.exception("Необработанная ошибка %s", error_id, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Что-то пошло не так. Код ошибки: {error_id}.", "code": "internal"},
    )


api = APIRouter(prefix="/api")
api.include_router(auth.router)
api.include_router(inventory.router)
api.include_router(production.router)
api.include_router(chat.router)
api.include_router(audit.router)


@api.get("/health", tags=["Служебное"], summary="Проверка, что сервис жив")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api)
