from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import AuthError
from app.models import User
from app.security import SESSION_COOKIE, decode_access_token
from app.services.fx import FxClient

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _token_from(request: Request) -> str | None:
    # Браузер присылает httpOnly-cookie; скрипты (эвалы, curl) — заголовок Authorization.
    if token := request.cookies.get(SESSION_COOKIE):
        return token
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    return value if scheme.lower() == "bearer" and value else None


async def get_current_user(request: Request, session: SessionDep) -> User:
    token = _token_from(request)
    user_id = decode_access_token(token) if token else None
    user = await session.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise AuthError("Войдите в систему.")
    return user


def get_fx(request: Request) -> FxClient:
    return request.app.state.fx


CurrentUser = Annotated[User, Depends(get_current_user)]
FxDep = Annotated[FxClient, Depends(get_fx)]
