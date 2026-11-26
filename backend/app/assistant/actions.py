"""Действия ассистента: предложить → показать → выполнить только после «Подтвердить».

Выполнение идёт через те же функции services/jobs.py, что и кнопки в интерфейсе,
поэтому права, проверки и журнал изменений одинаковые.
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.errors import DomainError, NotFoundError
from app.formatting import fmt_date, fmt_qty
from app.models import ActionStatus, JobStatus, PendingAction, User
from app.schemas.production import JobIn
from app.services import jobs, recipes
from app.services.permissions import Perm, require

TTL = timedelta(minutes=30)

CREATE_JOB = "create_and_plan_job"
CANCEL_JOB = "cancel_job"


async def propose_create_job(
    session: AsyncSession,
    user: User,
    *,
    conversation_id: int,
    message_id: int | None,
    recipe_name: str,
    data_qty: int,
    planned_date,
) -> PendingAction:
    require(user, Perm.JOB_MANAGE)
    recipe = await recipes.find_by_name(session, user, recipe_name)
    data = JobIn(recipe_id=recipe.id, qty=data_qty, planned_date=planned_date)
    plan = await jobs.preview_plan(session, user, data)
    preview = {
        "title": f"Создать и запланировать задание: {recipe.name}, {data.qty} шт "
        f"на {fmt_date(data.planned_date)}",
        "components": [
            {
                "material": i.material.name,
                "required": fmt_qty(i.qty_required, i.material.unit),
                "available": fmt_qty(i.qty_available, i.material.unit),
                "deficit": fmt_qty(i.qty_deficit, i.material.unit) if i.qty_deficit else None,
            }
            for i in plan.items
        ],
        "warning": "Материалов не хватает: задание будет запланировано с дефицитом, "
        "запустить его получится после пополнения."
        if plan.has_deficit
        else None,
    }
    return await _save(
        session, user, conversation_id, message_id, CREATE_JOB, data.model_dump(mode="json"), preview
    )


async def propose_cancel_job(
    session: AsyncSession, user: User, *, conversation_id: int, message_id: int | None, job_number: str
) -> PendingAction:
    require(user, Perm.JOB_CANCEL)
    job = await jobs.find_by_number(session, user, job_number)
    if job.status not in (JobStatus.DRAFT, JobStatus.PLANNED):
        raise DomainError(f"Нельзя отменить: задание {job.number} в статусе «{job.status.label}».")
    card = await jobs.get_card(session, user, job.id)
    preview = {
        "title": f"Отменить задание {job.number}: {card.recipe_name}, {card.qty} шт "
        f"на {fmt_date(card.planned_date)}",
        "components": [],
        "warning": "Резервы под задание будут сняты." if card.reservations else None,
    }
    return await _save(session, user, conversation_id, message_id, CANCEL_JOB, {"job_id": job.id}, preview)


async def _save(
    session: AsyncSession,
    user: User,
    conversation_id: int,
    message_id: int | None,
    tool: str,
    args: dict[str, Any],
    preview: dict[str, Any],
) -> PendingAction:
    action = PendingAction(
        user_id=user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        tool=tool,
        args=args,
        preview=preview,
        status=ActionStatus.PENDING,
        expires_at=now() + TTL,
    )
    session.add(action)
    await session.flush()
    return action


async def _lock(session: AsyncSession, user: User, action_id: int) -> PendingAction:
    """Чужое действие для пользователя не существует. Блокировка строки — защита от двойного клика."""
    action = await session.scalar(
        select(PendingAction)
        .where(PendingAction.id == action_id, PendingAction.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if action is None:
        raise NotFoundError("Предложение не найдено.")
    if action.status != ActionStatus.PENDING:
        raise DomainError("Это предложение уже обработано.")
    return action


async def confirm(session: AsyncSession, user: User, action_id: int) -> PendingAction:
    """Выполняет действие. Права и данные проверяются заново: с момента предложения всё могло измениться."""
    action = await _lock(session, user, action_id)
    if action.expires_at < now():
        action.status = ActionStatus.EXPIRED
        action.result = "Предложение устарело. Попросите ассистента подготовить его заново."
        return action
    try:
        async with session.begin_nested():  # при ошибке откатится только само действие
            action.result = await _execute(session, user, action)
        action.status = ActionStatus.CONFIRMED
    except DomainError as exc:
        action.status = ActionStatus.FAILED
        action.result = exc.message
    await session.flush()
    return action


async def reject(session: AsyncSession, user: User, action_id: int) -> PendingAction:
    action = await _lock(session, user, action_id)
    action.status = ActionStatus.REJECTED
    action.result = "Вы отклонили предложение, ничего не изменилось."
    await session.flush()
    return action


async def _execute(session: AsyncSession, user: User, action: PendingAction) -> str:
    if action.tool == CREATE_JOB:
        job = await jobs.create_draft(session, user, JobIn.model_validate(action.args))
        await jobs.plan(session, user, job.id)
        card = await jobs.get_card(session, user, job.id)
        text = f"Задание {card.number} создано и запланировано: {card.recipe_name}, {card.qty} шт."
        if missing := [c for c in card.components if c.qty_deficit > 0]:
            parts = [f"{fmt_qty(c.qty_deficit, c.material.unit)} — {c.material.name}" for c in missing]
            text += f" Дефицит: {'; '.join(parts)}."
        return text
    if action.tool == CANCEL_JOB:
        job = await jobs.cancel(session, user, action.args["job_id"])
        return f"Задание {job.number} отменено, резервы сняты."
    raise DomainError("Неизвестное действие.")
