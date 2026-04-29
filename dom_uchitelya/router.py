from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import Article
from schemas import ArticleCreate, ArticleListResponse, ArticleResponse, ArticleUpdate

router = APIRouter(tags=["dom-uchitelya"])

COMMON_PUBLIC_SCOPES = ("imcro_only", "both")
DOMU_PUBLIC_SCOPES = ("dom_uchitelya_only", "both")
COMMON_ADMIN_ROLES = {"admin", "methodist"}
DOMU_ADMIN_ROLES = {"admin", "methodist", "domu_editor"}
DOMU_EDITOR_ALLOWED_SCOPES = {"both", "dom_uchitelya_only"}
ARTICLE_COVER_DIR = Path("static/articles/covers")
ALLOWED_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


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
    return _require_roles(current_user, COMMON_ADMIN_ROLES)


def require_domu_admin(current_user=Depends(get_current_user)) -> str:
    return _require_roles(current_user, DOMU_ADMIN_ROLES)


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


def _ensure_methodist_article_access(role_name: str, user, payload: dict | None = None, article: Article | None = None):
    if role_name != "methodist":
        return
    allowed_subjects = _allowed_methodika_subjects(user)
    if not allowed_subjects:
        return
    subject = None
    if payload is not None and "methodika_subject" in payload:
        subject = payload.get("methodika_subject")
    if subject is None and article is not None:
        subject = article.methodika_subject
    if subject and subject not in allowed_subjects:
        raise HTTPException(status_code=403, detail="methodika_subject is not allowed for this methodist")


def _query_public_news(db: Session, scopes: tuple[str, str], limit: int, offset: int):
    now = datetime.now(timezone.utc)
    items = (
        db.query(Article)
        .filter(
            Article.status == "published",
            Article.publishing_scope.in_(scopes),
            ((Article.published_at == None) | (Article.published_at <= now)),  # noqa: E711
        )
        .order_by(Article.is_pinned.desc(), Article.published_at.desc(), Article.created_at.desc(), Article.id.desc())
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
        allowed_subjects = _allowed_methodika_subjects(user)
        if allowed_subjects:
            query = query.filter(Article.methodika_subject.in_(allowed_subjects))
    return {"items": query.order_by(Article.is_pinned.desc(), Article.updated_at.desc(), Article.id.desc()).all()}


def _create_article(db: Session, data: ArticleCreate, author_id: int | None = None) -> Article:
    article = Article(**_sync_legacy_article_payload(data.model_dump()), author_id=author_id)
    article.published_at = _published_now_if_needed(article.status, article.published_at)
    db.add(article)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Article slug already exists") from exc
    db.refresh(article)
    return article


def _update_article(db: Session, article_id: int, data: ArticleUpdate) -> Article:
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    update_data = _sync_legacy_article_payload(data.model_dump(exclude_unset=True))
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


@router.get("/api/admin/news/", response_model=ArticleListResponse)
def list_common_admin_news(
    role_name: str = Depends(require_common_admin),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _query_admin_news(db, role_name=role_name, user=current_user)


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
    return _create_article(db, payload, author_id=getattr(current_user, "id", None))


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
    return _update_article(db, article_id, data)


@router.delete("/api/admin/news/{article_id}/", status_code=204)
def delete_common_admin_news(
    article_id: int,
    _: str = Depends(require_common_admin),
    db: Session = Depends(get_db),
):
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
    _ensure_methodist_article_access(role_name, current_user, data.model_dump())
    return _create_article(db, data, author_id=getattr(current_user, "id", None))


@router.patch("/api/admin/dom-uchitelya/news/{article_id}/", response_model=ArticleResponse)
def update_domu_admin_news(
    article_id: int,
    data: ArticleUpdate,
    role_name: str = Depends(require_domu_admin),
    db: Session = Depends(get_db),
):
    if role_name == "domu_editor" and data.publishing_scope == "imcro_only":
        raise HTTPException(status_code=403, detail="publishing_scope is not allowed for domu_editor")
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if role_name == "domu_editor" and article.publishing_scope not in DOMU_EDITOR_ALLOWED_SCOPES:
        raise HTTPException(status_code=403, detail="Article is outside Дом учителя scope")
    return _update_article(db, article_id, data)


@router.delete("/api/admin/dom-uchitelya/news/{article_id}/", status_code=204)
def delete_domu_admin_news(
    article_id: int,
    role_name: str = Depends(require_domu_admin),
    db: Session = Depends(get_db),
):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if role_name == "domu_editor" and article.publishing_scope not in DOMU_EDITOR_ALLOWED_SCOPES:
        raise HTTPException(status_code=403, detail="Article is outside Дом учителя scope")
    _delete_article(db, article_id)
    return None


@router.post("/api/admin/news/upload-cover/")
async def upload_common_article_cover(
    file: UploadFile = File(...),
    _: str = Depends(require_common_admin),
):
    return {"url": await _save_article_cover(file)}


@router.post("/api/admin/dom-uchitelya/news/upload-cover/")
async def upload_domu_article_cover(
    file: UploadFile = File(...),
    _: str = Depends(require_domu_admin),
):
    return {"url": await _save_article_cover(file)}
