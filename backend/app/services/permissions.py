"""Права ролей — единственное место, где они описаны.

Проверка `require()` вызывается внутри сервисных функций, поэтому кнопка в интерфейсе
и инструмент ассистента проходят через одну и ту же проверку.
"""

from enum import StrEnum

from app.errors import PermissionDeniedError
from app.models import Role, User


class Perm(StrEnum):
    VIEW_STOCK = "view_stock"
    MATERIAL_EDIT = "material_edit"
    RECEIPT_CREATE = "receipt_create"
    BATCH_STATUS = "batch_status"
    RECIPE_EDIT = "recipe_edit"
    JOB_MANAGE = "job_manage"
    JOB_CANCEL = "job_cancel"
    JOB_COST_VIEW = "job_cost_view"
    AUDIT_VIEW = "audit_view"
    MARKING_RETRY = "marking_retry"


S, T, M = Role.STOREKEEPER, Role.TECHNOLOGIST, Role.MANAGER

PERMISSION_ROLES: dict[Perm, tuple[Role, ...]] = {
    Perm.VIEW_STOCK: (S, T, M),
    Perm.MATERIAL_EDIT: (S, M),
    Perm.RECEIPT_CREATE: (S, M),
    Perm.BATCH_STATUS: (S, M),
    Perm.RECIPE_EDIT: (T, M),
    Perm.JOB_MANAGE: (T, M),
    Perm.JOB_CANCEL: (T, M),
    Perm.JOB_COST_VIEW: (T, M),
    Perm.AUDIT_VIEW: (M,),
    Perm.MARKING_RETRY: (M,),
}

# Как действие звучит в сообщении об отказе: «Недостаточно прав: {что} может {кто}».
PERMISSION_TEXT: dict[Perm, str] = {
    Perm.VIEW_STOCK: "смотреть остатки",
    Perm.MATERIAL_EDIT: "заводить материалы",
    Perm.RECEIPT_CREATE: "оформлять поступления",
    Perm.BATCH_STATUS: "менять статус партии",
    Perm.RECIPE_EDIT: "менять рецепты",
    Perm.JOB_MANAGE: "создавать, планировать, запускать и завершать задания",
    Perm.JOB_CANCEL: "отменять задания",
    Perm.JOB_COST_VIEW: "смотреть себестоимость заданий",
    Perm.AUDIT_VIEW: "смотреть журнал изменений",
    Perm.MARKING_RETRY: "повторять отправку в сервис маркировки",
}


def has_perm(user: User, perm: Perm) -> bool:
    return user.role in PERMISSION_ROLES[perm]


def require(user: User, perm: Perm) -> None:
    if has_perm(user, perm):
        return
    raise PermissionDeniedError(f"Недостаточно прав: {PERMISSION_TEXT[perm]} может {who_can(perm)}.")


def who_can(perm: Perm) -> str:
    roles = [role.label for role in PERMISSION_ROLES[perm]]
    return roles[0] if len(roles) == 1 else ", ".join(roles[:-1]) + " или " + roles[-1]


def permissions_of(role: Role) -> list[str]:
    return [perm.value for perm, roles in PERMISSION_ROLES.items() if role in roles]
