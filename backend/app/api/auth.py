from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, SessionDep
from app.errors import AuthError
from app.models import Role, User
from app.schemas.common import StrictModel
from app.security import SESSION_COOKIE, create_access_token, verify_password
from app.services.permissions import permissions_of

router = APIRouter(prefix="/auth", tags=["Авторизация"])


class LoginIn(StrictModel):
    login: str
    password: str


class MeOut(BaseModel):
    id: int
    login: str
    full_name: str
    role: Role
    role_label: str
    permissions: list[str]
    access_token: str | None = None


def _me(user: User, token: str | None = None) -> MeOut:
    return MeOut(
        id=user.id,
        login=user.login,
        full_name=user.full_name,
        role=user.role,
        role_label=user.role.label,
        permissions=permissions_of(user.role),
        access_token=token,
    )


@router.post("/login", response_model=MeOut, summary="Вход по логину и паролю")
async def login(data: LoginIn, response: Response, session: SessionDep) -> MeOut:
    user = await session.scalar(select(User).where(User.login == data.login.strip().lower()))
    # Одинаковый ответ и для неверного логина, и для неверного пароля: не подсказываем, что угадано.
    if user is None or not user.is_active or not verify_password(data.password, user.password_hash):
        raise AuthError("Неверный логин или пароль.")
    token = create_access_token(user.id)
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_ttl_minutes * 60,
    )
    # Токен в теле — для скриптов (эвалы); браузер пользуется httpOnly-cookie.
    return _me(user, token)


@router.post("/logout", status_code=204, summary="Выход")
async def logout(response: Response) -> Response:
    response.delete_cookie(SESSION_COOKIE)
    response.status_code = 204
    return response


@router.get("/me", response_model=MeOut, summary="Текущий пользователь и его права")
async def me(user: CurrentUser) -> MeOut:
    return _me(user)
