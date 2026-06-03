from collections.abc import Iterable

from fastapi import Depends, HTTPException, status

from auth import get_current_user
from models import User

ADMIN_ROLES = frozenset({"admin"})
CERTIFICATE_MANAGER_ROLES = frozenset({"admin", "methodist", "metodist_editor"})
TPMPK_ADMIN_ROLES = frozenset({"admin", "operator", "tpmpk_admin", "tpmpk_operator"})
PERMISSION_LEVELS = {"none": 0, "view": 1, "edit": 2}
MODULE_KEYS = frozenset({
    "articles",
    "certificates",
    "certificate_templates",
    "users_roles",
    "tpmpk",
    "audit_log",
    "portal_settings",
})

DEFAULT_ROLE_PERMISSIONS = {
    "admin": {
        "articles": "edit",
        "certificates": "edit",
        "certificate_templates": "edit",
        "users_roles": "edit",
        "tpmpk": "edit",
        "audit_log": "view",
        "portal_settings": "edit",
    },
    "methodist": {
        "articles": "edit",
        "certificates": "edit",
        "certificate_templates": "edit",
        "users_roles": "none",
        "tpmpk": "none",
        "audit_log": "view",
        "portal_settings": "none",
    },
    "metodist_editor": {
        "articles": "edit",
        "certificates": "edit",
        "certificate_templates": "edit",
        "users_roles": "none",
        "tpmpk": "none",
        "audit_log": "view",
        "portal_settings": "none",
    },
    "operator": {
        "articles": "none",
        "certificates": "none",
        "certificate_templates": "none",
        "users_roles": "none",
        "tpmpk": "edit",
        "audit_log": "view",
        "portal_settings": "none",
    },
    "tpmpk_admin": {
        "articles": "none",
        "certificates": "none",
        "certificate_templates": "none",
        "users_roles": "none",
        "tpmpk": "edit",
        "audit_log": "view",
        "portal_settings": "none",
    },
    "tpmpk_operator": {
        "articles": "none",
        "certificates": "none",
        "certificate_templates": "none",
        "users_roles": "none",
        "tpmpk": "edit",
        "audit_log": "view",
        "portal_settings": "none",
    },
    "domu_editor": {
        "articles": "edit",
        "certificates": "none",
        "certificate_templates": "none",
        "users_roles": "none",
        "tpmpk": "none",
        "audit_log": "view",
        "portal_settings": "none",
    },
    "user": {
        "articles": "none",
        "certificates": "none",
        "certificate_templates": "none",
        "users_roles": "none",
        "tpmpk": "none",
        "audit_log": "none",
        "portal_settings": "none",
    },
}


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


def default_permissions_for_role(role_name: str | None) -> dict[str, str]:
    role = normalize_role_name(role_name)
    defaults = DEFAULT_ROLE_PERMISSIONS.get(role, DEFAULT_ROLE_PERMISSIONS["user"])
    return dict(defaults)


def normalize_role_permissions(
    permissions: dict | None,
    role_name: str | None = None,
) -> dict[str, str]:
    normalized = default_permissions_for_role(role_name)
    for module_key, level in (permissions or {}).items():
        key = str(module_key or "").strip()
        value = str(level or "none").strip().lower()
        if key in MODULE_KEYS and value in PERMISSION_LEVELS:
            normalized[key] = value
    return normalized


def user_permissions(user) -> dict[str, str]:
    role_name = user_role_name(user)
    role = getattr(user, "role", None)
    permissions = getattr(role, "permissions", None) if role is not None and not isinstance(role, str) else None
    return normalize_role_permissions(permissions, role_name)


def has_permission(user, module_key: str, min_level: str = "view") -> bool:
    required = PERMISSION_LEVELS.get(min_level, PERMISSION_LEVELS["view"])
    current = PERMISSION_LEVELS.get(user_permissions(user).get(module_key, "none"), 0)
    return current >= required


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


def require_permission(module_key: str, min_level: str = "view"):
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if getattr(current_user, "is_active", True) is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is inactive",
            )
        if not has_permission(current_user, module_key, min_level):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied",
            )
        return current_user

    return dependency


require_admin_user = require_roles(*ADMIN_ROLES)
require_certificate_manager_user = require_permission("certificates", "edit")
require_tpmpk_admin_user = require_permission("tpmpk", "edit")
