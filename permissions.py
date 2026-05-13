from collections.abc import Iterable

from fastapi import Depends, HTTPException, status

from auth import get_current_user
from models import User

ADMIN_ROLES = frozenset({"admin"})
CERTIFICATE_MANAGER_ROLES = frozenset({"admin", "methodist", "metodist_editor"})
TPMPK_ADMIN_ROLES = frozenset({"admin", "operator", "tpmpk_admin", "tpmpk_operator"})


def normalize_role_name(role_name: str | None) -> str:
    return str(role_name or "user").strip().lower() or "user"


def user_role_name(user) -> str:
    role = getattr(user, "role", None)
    if isinstance(role, str):
        return normalize_role_name(role)
    if role is not None and getattr(role, "role_name", None):
        return normalize_role_name(role.role_name)
    if getattr(user, "role_name", None):
        return normalize_role_name(user.role_name)
    return "user"


def ensure_role(user, allowed_roles: Iterable[str]) -> User:
    if getattr(user, "is_active", True) is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )

    allowed = {normalize_role_name(role) for role in allowed_roles}
    if user_role_name(user) not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    return user


def require_roles(*allowed_roles: str):
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        return ensure_role(current_user, allowed_roles)

    return dependency


require_admin_user = require_roles(*ADMIN_ROLES)
require_certificate_manager_user = require_roles(*CERTIFICATE_MANAGER_ROLES)
require_tpmpk_admin_user = require_roles(*TPMPK_ADMIN_ROLES)
