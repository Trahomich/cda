"""Зависимости API (MVP-4): типизированный текущий пользователь из JWT."""

from app.api.deps import get_current_user
from typing import Annotated
from fastapi import Depends
from app.models.auth import User

# Аннотация для эндпоинтов: `user: CurrentUser` (FastAPI подставит Depends).
CurrentUser = Annotated[User, Depends(get_current_user)]
