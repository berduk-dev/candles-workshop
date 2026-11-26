"""Рецепты, задания, отчёты. Роутеры тонкие: разбор запроса → сервис → commit."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.deps import CurrentUser, FxDep, SessionDep
from app.models import Currency, JobStatus
from app.schemas.common import Page
from app.schemas.production import (
    DeficitOut,
    JobCardOut,
    JobCompleteIn,
    JobCostOut,
    JobIn,
    JobOut,
    PlanPreview,
    RecipeCostOut,
    RecipeIn,
    RecipeOut,
)
from app.services import analytics, jobs, marking, recipes

router = APIRouter(tags=["Производство"])


@router.get("/recipes", response_model=list[RecipeOut], summary="Рецепты")
async def list_recipes(session: SessionDep, user: CurrentUser):
    return await recipes.list_recipes(session, user)


@router.post("/recipes", response_model=RecipeOut, status_code=201, summary="Создать рецепт")
async def create_recipe(data: RecipeIn, session: SessionDep, user: CurrentUser):
    result = await recipes.save(session, user, data)
    await session.commit()
    return result


@router.put("/recipes/{recipe_id}", response_model=RecipeOut, summary="Изменить рецепт")
async def update_recipe(recipe_id: int, data: RecipeIn, session: SessionDep, user: CurrentUser):
    result = await recipes.save(session, user, data, recipe_id)
    await session.commit()
    return result


@router.get("/recipes/{recipe_id}/cost", response_model=RecipeCostOut, summary="Себестоимость изделия")
async def recipe_cost(
    recipe_id: int, session: SessionDep, user: CurrentUser, fx: FxDep, currency: Currency = Currency.RUB
):
    result = await analytics.recipe_cost(session, user, recipe_id, currency, fx)
    await session.commit()  # мог добавиться курс в кэш
    return result


@router.get("/jobs", response_model=Page[JobOut], summary="Задания")
async def list_jobs(
    session: SessionDep,
    user: CurrentUser,
    status: Annotated[list[JobStatus] | None, Query()] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    overdue: bool = False,
    sort: str = "-planned_date",
    page: int = 1,
    page_size: int = 20,
):
    return await jobs.list_jobs(
        session,
        user,
        status=status,
        date_from=date_from,
        date_to=date_to,
        search=search,
        overdue=overdue,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.post("/jobs/preview", response_model=PlanPreview, summary="Пробный расчёт без записи")
async def preview_job(data: JobIn, session: SessionDep, user: CurrentUser):
    return await jobs.preview_plan(session, user, data)


@router.post("/jobs", response_model=JobCardOut, status_code=201, summary="Создать черновик задания")
async def create_job(data: JobIn, session: SessionDep, user: CurrentUser):
    job = await jobs.create_draft(session, user, data)
    await session.commit()
    return await jobs.get_card(session, user, job.id)


@router.get("/jobs/{job_id}", response_model=JobCardOut, summary="Карточка задания")
async def get_job(job_id: int, session: SessionDep, user: CurrentUser):
    return await jobs.get_card(session, user, job_id)


@router.put("/jobs/{job_id}", response_model=JobCardOut, summary="Изменить черновик")
async def update_job(job_id: int, data: JobIn, session: SessionDep, user: CurrentUser):
    await jobs.update_draft(session, user, job_id, data)
    await session.commit()
    return await jobs.get_card(session, user, job_id)


@router.post("/jobs/{job_id}/plan", response_model=JobCardOut, summary="Запланировать (резерв по FIFO)")
async def plan_job(job_id: int, session: SessionDep, user: CurrentUser):
    await jobs.plan(session, user, job_id)
    await session.commit()
    return await jobs.get_card(session, user, job_id)


@router.post("/jobs/{job_id}/reserve", response_model=JobCardOut, summary="Дозарезервировать недостающее")
async def reserve_job(job_id: int, session: SessionDep, user: CurrentUser):
    await jobs.reserve_more(session, user, job_id)
    await session.commit()
    return await jobs.get_card(session, user, job_id)


@router.post("/jobs/{job_id}/start", response_model=JobCardOut, summary="Запустить")
async def start_job(job_id: int, session: SessionDep, user: CurrentUser):
    await jobs.start(session, user, job_id)
    await session.commit()
    return await jobs.get_card(session, user, job_id)


@router.post("/jobs/{job_id}/complete", response_model=JobCardOut, summary="Завершить с фактическим расходом")
async def complete_job(job_id: int, data: JobCompleteIn, session: SessionDep, user: CurrentUser):
    await jobs.complete(session, user, job_id, data)
    await session.commit()
    return await jobs.get_card(session, user, job_id)


@router.post("/jobs/{job_id}/cancel", response_model=JobCardOut, summary="Отменить")
async def cancel_job(job_id: int, session: SessionDep, user: CurrentUser):
    await jobs.cancel(session, user, job_id)
    await session.commit()
    return await jobs.get_card(session, user, job_id)


@router.post("/jobs/{job_id}/marking/retry", response_model=JobCardOut, summary="Повторить отправку отчёта")
async def retry_marking(job_id: int, session: SessionDep, user: CurrentUser):
    await marking.retry(session, user, job_id)
    await session.commit()
    return await jobs.get_card(session, user, job_id)


@router.get("/jobs/{job_id}/cost", response_model=JobCostOut, summary="Себестоимость задания")
async def job_cost(
    job_id: int, session: SessionDep, user: CurrentUser, fx: FxDep, currency: Currency = Currency.RUB
):
    await jobs.get_card(session, user, job_id)  # понятное «Задание не найдено»
    result = await analytics.job_cost(session, user, job_id, currency, fx)
    await session.commit()
    return result


@router.get("/deficit", response_model=DeficitOut, summary="Дефицит по заданиям за период")
async def deficit(session: SessionDep, user: CurrentUser, date_from: date, date_to: date):
    return await analytics.deficit(session, user, date_from, date_to)
