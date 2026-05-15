"""
assistant_access.py — политика доступа для публичного и сотруднического
контуров виртуального ассистента.

Обычный пользователь видит публичные страницы, ссылки и документы.
Сотрудник дополнительно получает доступ к документам, помеченным как internal.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Iterable
from typing import Mapping

from config import (
    ASSISTANT_INTERNAL_S3_KEYWORDS,
    ASSISTANT_INTERNAL_S3_PREFIXES,
)

PUBLIC_SCOPE = "public"
EMPLOYEE_SCOPE = "employee"
PUBLIC_ACCESS = "public"
INTERNAL_ACCESS = "internal"


def normalize_role_name(role_name: str | None) -> str:
    return (role_name or "").strip().lower()


def access_scope_for_internal_docs_permission(can_access_internal_docs: bool | None) -> str:
    return EMPLOYEE_SCOPE if bool(can_access_internal_docs) else PUBLIC_SCOPE


def access_scope_for_role(
    role_name: str | None = None,
    *,
    can_access_internal_docs: bool | None = False,
) -> str:
    """Return the assistant access scope.

    Role names are kept only for API compatibility. Access to internal
    documents is controlled by the explicit DB permission flag.
    """
    return access_scope_for_internal_docs_permission(can_access_internal_docs)


def infer_s3_access_level(s3_key: str | None) -> str:
    key = (s3_key or "").strip().lower().replace("\\", "/")
    if not key:
        return PUBLIC_ACCESS

    if any(key.startswith(prefix) for prefix in ASSISTANT_INTERNAL_S3_PREFIXES):
        return INTERNAL_ACCESS

    normalized = re.sub(r"[\s_\-.]+", " ", key)
    if any(keyword in normalized for keyword in ASSISTANT_INTERNAL_S3_KEYWORDS):
        return INTERNAL_ACCESS

    return PUBLIC_ACCESS


def s3_key_folder(s3_key: str | None) -> str:
    key = (s3_key or "").strip().replace("\\", "/")
    if not key:
        return ""
    folder = str(PurePosixPath(key).parent)
    return "" if folder == "." else folder


def s3_key_filename(s3_key: str | None) -> str:
    key = (s3_key or "").strip().replace("\\", "/")
    return PurePosixPath(key).name if key else ""


def find_s3_folder_conflicts(s3_keys: Iterable[str]) -> list[dict]:
    """
    Находит файлы с одинаковым именем, которые одновременно лежат в разных
    папках S3. Такие документы считаются неоднозначными и не индексируются.
    """
    by_filename: dict[str, list[str]] = {}
    for key in s3_keys:
        filename = s3_key_filename(key)
        if not filename:
            continue
        by_filename.setdefault(filename.casefold(), []).append(key)

    conflicts: list[dict] = []
    for keys in by_filename.values():
        folders = sorted({s3_key_folder(key) for key in keys})
        if len(folders) <= 1:
            continue
        sorted_keys = sorted(keys)
        filename = s3_key_filename(sorted_keys[0])
        message = (
            f"Файл «{filename}» найден одновременно в нескольких папках: "
            f"{', '.join(folder or '/' for folder in folders)}. "
            "Он не добавлен в индекс ассистента."
        )
        conflicts.append(
            {
                "filename": filename,
                "folders": folders,
                "keys": sorted_keys,
                "message": message,
            }
        )

    return sorted(conflicts, key=lambda item: item["filename"].casefold())


def document_access_level(metadata: Mapping | None) -> str:
    meta = metadata or {}
    raw_level = (
        meta.get("access_level")
        or meta.get("visibility")
        or meta.get("assistant_access")
    )
    level = str(raw_level or "").strip().lower()
    if level in {INTERNAL_ACCESS, "employee", "staff", "private", "restricted"}:
        return INTERNAL_ACCESS
    if level in {PUBLIC_ACCESS, "open"}:
        return PUBLIC_ACCESS

    return infer_s3_access_level(meta.get("s3_key"))


def can_access_document(metadata: Mapping | None, access_scope: str) -> bool:
    if document_access_level(metadata) == PUBLIC_ACCESS:
        return True
    return access_scope == EMPLOYEE_SCOPE


def scoped_session_id(session_id: str, access_scope: str, user_id: int | None) -> str:
    clean_session = (session_id or "default").strip() or "default"
    clean_scope = EMPLOYEE_SCOPE if access_scope == EMPLOYEE_SCOPE else PUBLIC_SCOPE
    principal = str(user_id) if user_id is not None else "anonymous"
    return f"{clean_scope}:{principal}:{clean_session}"
