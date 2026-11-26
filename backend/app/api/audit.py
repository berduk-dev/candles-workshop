from fastapi import APIRouter

from app.deps import CurrentUser, SessionDep
from app.schemas.common import Page
from app.schemas.inventory import AuditEntryOut
from app.services import audit

router = APIRouter(tags=["Журнал изменений"])


@router.get("/audit", response_model=Page[AuditEntryOut], summary="Журнал изменений (руководитель)")
async def list_audit(
    session: SessionDep,
    user: CurrentUser,
    entity: str | None = None,
    entity_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
):
    page_size = max(1, min(page_size, 200))
    rows, total = await audit.list_entries(
        session, user, entity=entity, entity_id=entity_id, page=max(page, 1), page_size=page_size
    )
    return Page[AuditEntryOut](
        items=[AuditEntryOut.model_validate(r, from_attributes=True) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
