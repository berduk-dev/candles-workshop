"""Мок сервиса маркировки — намеренно ненадёжный.

30% ответов — 500, 10% — ответ через 10 секунд. Как настоящий сервис, он помнит ключи
идемпотентности: повтор с тем же ключом возвращает прежний результат и не создаёт второй отчёт.
"""

import asyncio
import os
import random
import uuid

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Мок сервиса маркировки")

FAIL_RATE = float(os.environ.get("FAIL_RATE", "0.3"))
SLOW_RATE = float(os.environ.get("SLOW_RATE", "0.1"))
SLOW_SECONDS = float(os.environ.get("SLOW_SECONDS", "10"))

reports: dict[str, dict] = {}  # ключ идемпотентности → принятый отчёт


class ReportIn(BaseModel):
    job_number: str
    product: str
    quantity: int
    completed_at: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/reports")
async def create_report(data: ReportIn, idempotency_key: str = Header()) -> dict:
    if random.random() < FAIL_RATE:
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервиса маркировки")
    # Сначала принимаем отчёт, потом «думаем»: клиент может не дождаться ответа, хотя отчёт уже
    # записан. Именно для этого случая и нужен ключ идемпотентности.
    if idempotency_key not in reports:
        reports[idempotency_key] = {"id": f"MRK-{uuid.uuid4().hex[:8]}", **data.model_dump()}
    if random.random() < SLOW_RATE:
        await asyncio.sleep(SLOW_SECONDS)
    return {"id": reports[idempotency_key]["id"], "status": "accepted"}


@app.get("/reports")
def list_reports() -> list[dict]:
    return list(reports.values())
