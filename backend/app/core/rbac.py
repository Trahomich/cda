"""RBAC: доступ ролей к API-группам по матрице прав docs/07-ux.md §5.

Матрица: | Экран | Агроном | Руководитель | Механизатор | Зоотехник/вет. | Админ |
Ниже каждая группа перечисляет роли, имеющие доступ хотя бы на чтение
к соответствующим экранам/действиям (в скобках — экраны матрицы).
Ограничения «своё»/«свои задания» (E-01, E-03, E-07, E-11, E-13, E-17)
дополнительно фильтруются на уровне запросов модулей, а не RBAC-группы.
"""

from collections.abc import Callable, Iterable

from fastapi import Depends

from app.api.deps import CurrentUser
from app.core.errors import AppError
from app.models.auth import User, UserRole

# API-группа -> роли с доступом (читаются по матрице 07-ux §5)
API_GROUP_ROLES: dict[str, frozenset[UserRole]] = {
    # E-01: Ч для всех ролей
    "dashboard": frozenset(UserRole),
    # E-02, E-03: А, Р, Д (+ М — журнал поля, фильтруется в модуле)
    "fields": frozenset({UserRole.AGRONOMIST, UserRole.MANAGER, UserRole.MACHINE_OPERATOR,
                         UserRole.ADMIN}),
    # E-04: А, Р, Д
    "rotation": frozenset({UserRole.AGRONOMIST, UserRole.MANAGER, UserRole.ADMIN}),
    # E-05: А, Р, Д
    "inputs": frozenset({UserRole.AGRONOMIST, UserRole.MANAGER, UserRole.ADMIN}),
    # E-07, E-08, E-17: А, Р, М, Д
    "operations": frozenset({UserRole.AGRONOMIST, UserRole.MANAGER,
                             UserRole.MACHINE_OPERATOR, UserRole.ADMIN}),
    # E-06: А, Р, М, Д; E-10: все 5 ролей
    "weather": frozenset(UserRole),
    # E-09: А, Р, Д
    "satellite": frozenset({UserRole.AGRONOMIST, UserRole.MANAGER, UserRole.ADMIN}),
    # E-11: А, Р, М, Д
    "machinery": frozenset({UserRole.AGRONOMIST, UserRole.MANAGER,
                            UserRole.MACHINE_OPERATOR, UserRole.ADMIN}),
    # E-13: все 5 ролей (М и З — только свои)
    "alerts": frozenset(UserRole),
    # E-14: Р (чтение), Д (чтение/управление)
    "sources": frozenset({UserRole.MANAGER, UserRole.ADMIN}),
    # E-15: А, Р, З (корма), Д
    "references": frozenset({UserRole.AGRONOMIST, UserRole.MANAGER,
                             UserRole.LIVESTOCK_SPECIALIST, UserRole.ADMIN}),
    # E-16: Р (чтение), Д (чтение/правка)
    "users": frozenset({UserRole.MANAGER, UserRole.ADMIN}),
    # «Экспорт данных» (последняя строка матрицы): Р, Д
    "export": frozenset({UserRole.MANAGER, UserRole.ADMIN}),
}


def role_checker(*allowed: UserRole) -> Callable[..., User]:
    """Фабрика зависимости FastAPI: пускать только перечисленные роли (403 иначе).

    Пример: `router = APIRouter(dependencies=[Depends(role_checker(UserRole.ADMIN))])`.
    """

    allowed_set = frozenset(allowed)

    def _check(user: CurrentUser) -> User:
        if user.role not in allowed_set:
            raise AppError(
                f"Роль «{user.role.value}» не имеет доступа к ресурсу",
                code="insufficient_role",
                title="Доступ запрещён",
                status_code=403,
            )
        return user

    return _check


def group_checker(group: str) -> Callable[..., User]:
    """Зависимость по имени API-группы из API_GROUP_ROLES (см. матрицу 07-ux §5)."""
    if group not in API_GROUP_ROLES:
        raise ValueError(f"Неизвестная API-группа RBAC: {group}")
    return role_checker(*API_GROUP_ROLES[group])


def roles_of(group: str) -> Iterable[UserRole]:
    """Роли группы (для документации и тестов)."""
    return API_GROUP_ROLES[group]


__all__ = ["API_GROUP_ROLES", "group_checker", "role_checker", "roles_of", "Depends"]
