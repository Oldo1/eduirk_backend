from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import Article
from permissions import has_permission
from schemas import ArticleCreate, ArticleListResponse, ArticleResponse, ArticleUpdate

router = APIRouter(tags=["dom-uchitelya"])

COMMON_PUBLIC_SCOPES = ("imcro_only", "both")
DOMU_PUBLIC_SCOPES = ("dom_uchitelya_only", "both")
COMMON_ADMIN_ROLES = {"admin", "methodist"}
DOMU_ADMIN_ROLES = {"admin", "methodist", "domu_editor"}
DOMU_EDITOR_ALLOWED_SCOPES = {"both", "dom_uchitelya_only"}
MAIN_DUPLICATION_ROLES = {"admin", "methodist"}
ARTICLE_COVER_DIR = Path("static/articles/covers")
ARTICLE_ATTACHMENT_DIR = Path("static/articles/attachments")
ALLOWED_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_ATTACHMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}


def _user_role_name(user) -> str:
    role = getattr(user, "role", None)
    if isinstance(role, str):
        return role
    if role is not None and getattr(role, "role_name", None):
        return role.role_name
    return getattr(user, "role_name", None) or "user"


def _require_roles(user, allowed_roles: set[str]) -> str:
    role_name = _user_role_name(user)
    if role_name not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return role_name


def require_common_admin(current_user=Depends(get_current_user)) -> str:
    role_name = _require_roles(current_user, COMMON_ADMIN_ROLES)
    if getattr(current_user, "is_active", True) is False or not has_permission(current_user, "articles", "edit"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return role_name


def require_domu_admin(current_user=Depends(get_current_user)) -> str:
    role_name = _require_roles(current_user, DOMU_ADMIN_ROLES)
    if getattr(current_user, "is_active", True) is False or not has_permission(current_user, "articles", "edit"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return role_name


def _published_now_if_needed(status_value: str | None, current_value):
    if status_value == "published" and current_value is None:
        return datetime.now(timezone.utc)
    return current_value


def _sync_legacy_article_payload(data: dict) -> dict:
    payload = dict(data)
    if payload.get("lead") is not None and "excerpt" not in payload:
        payload["excerpt"] = payload["lead"]
    if payload.get("excerpt") is not None and "lead" not in payload:
        payload["lead"] = payload["excerpt"]
    if payload.get("cover_image_url") is not None and "image" not in payload:
        payload["image"] = payload["cover_image_url"]
    if payload.get("image") is not None and "cover_image_url" not in payload:
        payload["cover_image_url"] = payload["image"]
    return payload


def _allowed_methodika_subjects(user) -> list[str]:
    value = getattr(user, "allowed_methodika_subjects", None) or []
    return [str(item) for item in value if str(item).strip()]


def _user_id(user) -> int | None:
    value = getattr(user, "id", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ensure_article_owner(role_name: str, user, article: Article):
    if role_name not in {"methodist", "domu_editor"}:
        return
    user_id = _user_id(user)
    if user_id is None or article.author_id != user_id:
        raise HTTPException(status_code=403, detail="Article belongs to another author")


def _ensure_methodist_article_access(role_name: str, user, payload: dict | None = None, article: Article | None = None):
    if role_name != "methodist":
        return
    if article is not None:
        _ensure_article_owner(role_name, user, article)
    allowed_subjects = _allowed_methodika_subjects(user)
    if not allowed_subjects:
        return
    if payload is not None and payload.get("hub_kind") == "methodika" and not payload.get("methodika_subject"):
        raise HTTPException(status_code=403, detail="methodist can publish methodika materials only with allowed methodika_subject")
    subject = None
    if payload is not None and "methodika_subject" in payload:
        subject = payload.get("methodika_subject")
    if subject is None and article is not None:
        subject = article.methodika_subject
    if subject and subject not in allowed_subjects:
        raise HTTPException(status_code=403, detail="methodika_subject is not allowed for this methodist")


def _is_home_article(values: dict) -> bool:
    return not any(
        [
            values.get("methodika_subject"),
            values.get("dom_uchitelya_section"),
            values.get("noko_section"),
            values.get("hub_kind"),
            values.get("hub_path"),
        ]
    )


def _section_key(values: dict) -> str:
    if values.get("dom_uchitelya_section"):
        return f"domu:{values['dom_uchitelya_section']}"
    if values.get("methodika_subject"):
        return f"methodika_subject:{values['methodika_subject']}"
    if values.get("hub_kind") == "methodika" and values.get("hub_path"):
        return f"methodika_section:{values['hub_path']}"
    if values.get("noko_section"):
        return f"noko:{values['noko_section']}"
    if values.get("hub_kind") and values.get("hub_path"):
        return f"{values['hub_kind']}:{values['hub_path']}"
    if values.get("hub_kind"):
        return f"{values['hub_kind']}:root"
    return "home"


def _pin_target_keys(values: dict) -> set[str]:
    keys = {f"section:{_section_key(values)}"}
    if values.get("duplicate_to_main") or _is_home_article(values):
        keys.add("main_news")
    if values.get("duplicate_to_events"):
        keys.add("events")
    return keys


def _extract_article_values(article: Article) -> dict:
    return {
        "status": article.status,
        "publishing_scope": article.publishing_scope,
        "is_pinned": bool(article.is_pinned),
        "duplicate_to_main": bool(article.duplicate_to_main),
        "duplicate_to_events": bool(article.duplicate_to_events),
        "methodika_subject": article.methodika_subject,
        "dom_uchitelya_section": article.dom_uchitelya_section,
        "noko_section": article.noko_section,
        "hub_kind": article.hub_kind,
        "hub_path": article.hub_path,
    }


def _ensure_domu_editor_article_access(role_name: str, user, payload: dict | None = None, article: Article | None = None):
    if role_name != "domu_editor":
        return
    values = _extract_article_values(article) if article is not None else {}
    if article is not None:
        _ensure_article_owner(role_name, user, article)
    if payload:
        values.update(payload)

    if values.get("publishing_scope") not in DOMU_EDITOR_ALLOWED_SCOPES:
        raise HTTPException(status_code=403, detail="publishing_scope is not allowed for domu_editor")
    if not values.get("dom_uchitelya_section"):
        raise HTTPException(status_code=400, detail="dom_uchitelya_section is required")
    if any(values.get(key) for key in ("methodika_subject", "noko_section", "hub_kind", "hub_path")):
        raise HTTPException(status_code=403, detail="domu_editor can publish only to Dom uchitelya sections")


def _ensure_pin_limits(db: Session, payload: dict, current_article_id: int | None = None):
    if not payload.get("is_pinned"):
        return
    target_keys = _pin_target_keys(payload)
    pinned_articles = (
        db.query(Article)
        .filter(Article.is_pinned == True, Article.status != "archive")  # noqa: E712
        .all()
    )
    for article in pinned_articles:
        if current_article_id is not None and article.id == current_article_id:
            continue
        keys = _pin_target_keys(_extract_article_values(article))
        for key in target_keys:
            if key in keys:
                count = sum(1 for row in pinned_articles if row.id != current_article_id and key in _pin_target_keys(_extract_article_values(row)))
                if count >= 3:
                    raise HTTPException(status_code=400, detail=f"Pinned limit reached for {key}. Maximum is 3.")


def _apply_main_duplication_policy(role_name: str, payload: dict, explicit_change: bool):
    if role_name not in MAIN_DUPLICATION_ROLES and explicit_change and payload.get("duplicate_to_main"):
        payload["duplicate_to_main"] = False


def _query_public_news(db: Session, scopes: tuple[str, str], limit: int, offset: int):
    now = datetime.now(timezone.utc)
    items = (
        db.query(Article)
        .filter(
            Article.status == "published",
            Article.publishing_scope.in_(scopes),
            ((Article.published_at == None) | (Article.published_at <= now)),  # noqa: E711
            (
                (Article.duplicate_to_main == True)  # noqa: E712
                | (
                    (Article.methodika_subject == None)  # noqa: E711
                    & (Article.dom_uchitelya_section == None)  # noqa: E711
                    & (Article.noko_section == None)  # noqa: E711
                    & (Article.hub_kind == None)  # noqa: E711
                )
            ),
        )
        .order_by(Article.is_pinned.desc(), Article.published_at.desc(), Article.created_at.desc(), Article.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {"items": items}


def _query_public_events(db: Session, scopes: tuple[str, str], limit: int, offset: int):
    now = datetime.now(timezone.utc)
    items = (
        db.query(Article)
        .filter(
            Article.status == "published",
            Article.publishing_scope.in_(scopes),
            Article.duplicate_to_events == True,  # noqa: E712
            ((Article.published_at == None) | (Article.published_at <= now)),  # noqa: E711
        )
        .order_by(Article.is_pinned.desc(), Article.published_at.desc(), Article.created_at.desc(), Article.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {"items": items}


def _query_public_hub_news(
    db: Session,
    scopes: tuple[str, str],
    hub: str,
    section: str | None,
    subject: str | None,
    limit: int,
    offset: int,
):
    now = datetime.now(timezone.utc)
    query = db.query(Article).filter(
        Article.status == "published",
        Article.publishing_scope.in_(scopes),
        ((Article.published_at == None) | (Article.published_at <= now)),  # noqa: E711
    )
    if hub == "methodika":
        query = query.filter((Article.methodika_subject != None) | (Article.hub_kind == "methodika"))  # noqa: E711
        if subject:
            query = query.filter(Article.methodika_subject == subject)
        elif section:
            query = query.filter(Article.hub_kind == "methodika", Article.hub_path == section)
    elif hub == "noko":
        query = query.filter(Article.noko_section != None)  # noqa: E711
        if section:
            query = query.filter(Article.noko_section == section)
    else:
        query = query.filter(Article.hub_kind == hub)
        if section:
            query = query.filter(Article.hub_path == section)
    items = (
        query.order_by(Article.is_pinned.desc(), Article.published_at.desc(), Article.created_at.desc(), Article.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {"items": items}


def _query_admin_news(db: Session, scopes: tuple[str, ...] | None = None, role_name: str | None = None, user=None):
    query = db.query(Article)
    if scopes is not None:
        query = query.filter(Article.publishing_scope.in_(scopes))
    if role_name == "methodist":
        user_id = _user_id(user)
        if user_id is None:
            return {"items": []}
        query = query.filter(Article.author_id == user_id)
        allowed_subjects = _allowed_methodika_subjects(user)
        if allowed_subjects:
            query = query.filter(Article.methodika_subject.in_(allowed_subjects))
    elif role_name == "domu_editor":
        user_id = _user_id(user)
        if user_id is None:
            return {"items": []}
        query = query.filter(
            Article.author_id == user_id,
            Article.dom_uchitelya_section != None,  # noqa: E711
            Article.methodika_subject == None,  # noqa: E711
            Article.noko_section == None,  # noqa: E711
            Article.hub_kind == None,  # noqa: E711
            Article.hub_path == None,  # noqa: E711
        )
    return {"items": query.order_by(Article.is_pinned.desc(), Article.updated_at.desc(), Article.id.desc()).all()}


def _unique_slug(db: Session, slug: str, article_id: int | None = None) -> str:
    base = (slug or "article").strip("-")[:150] or "article"
    candidate = base
    counter = 2
    while True:
        query = db.query(Article).filter(Article.slug == candidate)
        if article_id is not None:
            query = query.filter(Article.id != article_id)
        if query.first() is None:
            return candidate
        suffix = f"-{counter}"
        candidate = f"{base[:160 - len(suffix)]}{suffix}"
        counter += 1


def _create_article(db: Session, data: ArticleCreate, role_name: str, author_id: int | None = None) -> Article:
    payload = _sync_legacy_article_payload(data.model_dump())
    _apply_main_duplication_policy(role_name, payload, explicit_change=True)
    _ensure_pin_limits(db, payload)
    payload["slug"] = _unique_slug(db, payload["slug"])
    article = Article(**payload, author_id=author_id)
    article.published_at = _published_now_if_needed(article.status, article.published_at)
    db.add(article)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Article slug already exists") from exc
    db.refresh(article)
    return article


def _update_article(db: Session, article_id: int, data: ArticleUpdate, role_name: str) -> Article:
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    update_data = _sync_legacy_article_payload(data.model_dump(exclude_unset=True))
    if update_data:
        merged = _extract_article_values(article)
        merged.update(update_data)
        _apply_main_duplication_policy(role_name, merged, explicit_change=("duplicate_to_main" in update_data))
        _ensure_pin_limits(db, merged, current_article_id=article_id)
        update_data.update(
            {
                "duplicate_to_main": merged.get("duplicate_to_main", False),
                "duplicate_to_events": merged.get("duplicate_to_events", False),
            }
        )
    if "slug" in update_data and update_data["slug"]:
        update_data["slug"] = _unique_slug(db, update_data["slug"], article_id=article_id)
    for key, value in update_data.items():
        setattr(article, key, value)
    article.published_at = _published_now_if_needed(update_data.get("status"), article.published_at)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Article slug already exists") from exc
    db.refresh(article)
    return article


def _delete_article(db: Session, article_id: int) -> None:
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    db.delete(article)
    db.commit()


async def _save_article_cover(file: UploadFile) -> str:
    if file.content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Only JPG, PNG, WEBP, or GIF images are allowed")
    ARTICLE_COVER_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        suffix = ".jpg"
    filename = f"{uuid4().hex}{suffix}"
    target = ARTICLE_COVER_DIR / filename
    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image is too large")
    target.write_bytes(content)
    return f"/static/articles/covers/{filename}"


async def _save_article_attachment(file: UploadFile) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only PDF, Word, PowerPoint, or Excel files are allowed")
    ARTICLE_ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
    original_name = Path(file.filename or f"document{suffix}").name
    filename = f"{uuid4().hex}{suffix}"
    target = ARTICLE_ATTACHMENT_DIR / filename
    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File is too large")
    target.write_bytes(content)
    return {
        "url": f"/static/articles/attachments/{filename}",
        "name": original_name,
        "size": len(content),
        "type": suffix.lstrip(".").upper(),
    }


@router.get("/api/news/", response_model=ArticleListResponse)
def get_common_news(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return _query_public_news(db, COMMON_PUBLIC_SCOPES, limit, offset)


@router.get("/api/dom-uchitelya/news/", response_model=ArticleListResponse)
def get_domu_news(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return _query_public_news(db, DOMU_PUBLIC_SCOPES, limit, offset)


@router.get("/api/events/", response_model=ArticleListResponse)
def get_events_news(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return _query_public_events(db, COMMON_PUBLIC_SCOPES, limit, offset)


@router.get("/api/hub/news/", response_model=ArticleListResponse)
def get_hub_news(
    hub: str = Query(..., pattern="^(methodika|noko|konkursy|deyatelnost|archiv)$"),
    section: str | None = Query(None),
    subject: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return _query_public_hub_news(db, COMMON_PUBLIC_SCOPES, hub, section, subject, limit, offset)


@router.get("/api/admin/articles/", response_model=ArticleListResponse)
@router.get("/api/admin/news/", response_model=ArticleListResponse)
def list_common_admin_news(
    role_name: str = Depends(require_common_admin),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _query_admin_news(db, role_name=role_name, user=current_user)


@router.post("/api/admin/articles/", response_model=ArticleResponse, status_code=201)
@router.post("/api/admin/news/", response_model=ArticleResponse, status_code=201)
def create_common_admin_news(
    data: ArticleCreate,
    role_name: str = Depends(require_common_admin),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = data
    if "publishing_scope" not in data.model_fields_set:
        payload = data.model_copy(update={"publishing_scope": "imcro_only"})
    _ensure_methodist_article_access(role_name, current_user, payload.model_dump())
    return _create_article(db, payload, role_name=role_name, author_id=getattr(current_user, "id", None))


@router.patch("/api/admin/articles/{article_id}/", response_model=ArticleResponse)
@router.patch("/api/admin/news/{article_id}/", response_model=ArticleResponse)
def update_common_admin_news(
    article_id: int,
    data: ArticleUpdate,
    role_name: str = Depends(require_common_admin),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    _ensure_methodist_article_access(role_name, current_user, data.model_dump(exclude_unset=True), article)
    return _update_article(db, article_id, data, role_name=role_name)


@router.delete("/api/admin/articles/{article_id}/", status_code=204)
@router.delete("/api/admin/news/{article_id}/", status_code=204)
def delete_common_admin_news(
    article_id: int,
    role_name: str = Depends(require_common_admin),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    _ensure_article_owner(role_name, current_user, article)
    _delete_article(db, article_id)
    return None


@router.get("/api/admin/dom-uchitelya/news/", response_model=ArticleListResponse)
def list_domu_admin_news(
    role_name: str = Depends(require_domu_admin),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if role_name == "domu_editor":
        return _query_admin_news(db, tuple(DOMU_EDITOR_ALLOWED_SCOPES), role_name=role_name, user=current_user)
    return _query_admin_news(db, role_name=role_name, user=current_user)


@router.post("/api/admin/dom-uchitelya/news/", response_model=ArticleResponse, status_code=201)
def create_domu_admin_news(
    data: ArticleCreate,
    role_name: str = Depends(require_domu_admin),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if role_name == "domu_editor" and data.publishing_scope not in DOMU_EDITOR_ALLOWED_SCOPES:
        raise HTTPException(status_code=403, detail="publishing_scope is not allowed for domu_editor")
    if role_name == "domu_editor" and not data.dom_uchitelya_section:
        raise HTTPException(status_code=400, detail="dom_uchitelya_section is required")
    _ensure_domu_editor_article_access(role_name, current_user, data.model_dump())
    _ensure_methodist_article_access(role_name, current_user, data.model_dump())
    return _create_article(db, data, role_name=role_name, author_id=getattr(current_user, "id", None))


@router.patch("/api/admin/dom-uchitelya/news/{article_id}/", response_model=ArticleResponse)
def update_domu_admin_news(
    article_id: int,
    data: ArticleUpdate,
    role_name: str = Depends(require_domu_admin),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if role_name == "domu_editor" and data.publishing_scope == "imcro_only":
        raise HTTPException(status_code=403, detail="publishing_scope is not allowed for domu_editor")
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if role_name == "domu_editor" and article.publishing_scope not in DOMU_EDITOR_ALLOWED_SCOPES:
        raise HTTPException(status_code=403, detail="Article is outside Dom uchitelya scope")
    _ensure_domu_editor_article_access(role_name, current_user, data.model_dump(exclude_unset=True), article)
    _ensure_methodist_article_access(role_name, current_user, data.model_dump(exclude_unset=True), article)
    return _update_article(db, article_id, data, role_name=role_name)


@router.delete("/api/admin/dom-uchitelya/news/{article_id}/", status_code=204)
def delete_domu_admin_news(
    article_id: int,
    role_name: str = Depends(require_domu_admin),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if role_name == "domu_editor" and article.publishing_scope not in DOMU_EDITOR_ALLOWED_SCOPES:
        raise HTTPException(status_code=403, detail="Article is outside Dom uchitelya scope")
    _ensure_domu_editor_article_access(role_name, current_user, article=article)
    _ensure_methodist_article_access(role_name, current_user, article=article)
    _delete_article(db, article_id)
    return None


@router.post("/api/admin/articles/upload-cover/")
@router.post("/api/admin/news/upload-cover/")
async def upload_common_article_cover(
    file: UploadFile = File(...),
    _: str = Depends(require_common_admin),
):
    return {"url": await _save_article_cover(file)}


@router.post("/api/admin/articles/upload-attachment/")
@router.post("/api/admin/news/upload-attachment/")
async def upload_common_article_attachment(
    file: UploadFile = File(...),
    _: str = Depends(require_common_admin),
):
    return await _save_article_attachment(file)


@router.post("/api/admin/dom-uchitelya/news/upload-cover/")
async def upload_domu_article_cover(
    file: UploadFile = File(...),
    _: str = Depends(require_domu_admin),
):
    return {"url": await _save_article_cover(file)}


@router.post("/api/admin/dom-uchitelya/news/upload-attachment/")
async def upload_domu_article_attachment(
    file: UploadFile = File(...),
    _: str = Depends(require_domu_admin),
):
    return await _save_article_attachment(file)
