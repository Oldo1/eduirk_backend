import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import get_current_user
from database import Base, get_db
from dom_uchitelya.router import router
from models import Article


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(router)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        test_client.SessionLocal = TestingSessionLocal
        yield test_client


def _add_article(client, title, scope, status="published"):
    db = client.SessionLocal()
    try:
        article = Article(
            title=title,
            slug=title.lower().replace(" ", "-"),
            status=status,
            excerpt=f"{title} excerpt",
            image="/images/news1.jpg",
            lead=f"{title} lead",
            body=f"# {title}\n\nArticle body",
            cover_image_url="/images/news1.jpg",
            blocks=[],
            categories=[],
            tags=[],
            publishing_scope=scope,
            published_at=datetime.now(timezone.utc),
        )
        db.add(article)
        db.commit()
    finally:
        db.close()


def _add_article_obj(client, **kwargs):
    db = client.SessionLocal()
    try:
        article = Article(
            title=kwargs.pop("title", "Article"),
            slug=kwargs.pop("slug", "article"),
            status=kwargs.pop("status", "published"),
            publishing_scope=kwargs.pop("publishing_scope", "imcro_only"),
            excerpt=kwargs.pop("excerpt", None),
            image=kwargs.pop("image", None),
            blocks=kwargs.pop("blocks", []),
            categories=kwargs.pop("categories", []),
            tags=kwargs.pop("tags", []),
            published_at=kwargs.pop("published_at", datetime.now(timezone.utc)),
            **kwargs,
        )
        db.add(article)
        db.commit()
        db.refresh(article)
        return article.id
    finally:
        db.close()


def test_public_news_scope_filters(client):
    _add_article(client, "IMCRO only", "imcro_only")
    _add_article(client, "DOMU only", "dom_uchitelya_only")
    _add_article(client, "Both feeds", "both")

    common = client.get("/api/news/")
    domu = client.get("/api/dom-uchitelya/news/")

    assert common.status_code == 200
    assert [item["title"] for item in common.json()["items"]] == ["Both feeds", "IMCRO only"]
    assert domu.status_code == 200
    assert [item["title"] for item in domu.json()["items"]] == ["Both feeds", "DOMU only"]


def test_public_news_sorts_pinned_then_newest_and_hides_scheduled(client):
    now = datetime.now(timezone.utc)
    _add_article_obj(
        client,
        title="Older pinned",
        slug="older-pinned",
        is_pinned=True,
        published_at=now - timedelta(days=3),
    )
    _add_article_obj(
        client,
        title="Newest normal",
        slug="newest-normal",
        is_pinned=False,
        published_at=now - timedelta(hours=1),
    )
    _add_article_obj(
        client,
        title="Future scheduled",
        slug="future-scheduled",
        is_pinned=True,
        published_at=now + timedelta(days=1),
    )

    response = client.get("/api/news/")

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["Older pinned", "Newest normal"]


def test_domu_editor_cannot_publish_imcro_only_in_domu_admin(client):
    app = client.app
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=10, email="domu@example.test", role="domu_editor")

    response = client.post(
        "/api/admin/dom-uchitelya/news/",
        json={
            "title": "Wrong scope",
            "slug": "wrong-scope",
            "status": "published",
            "publishing_scope": "imcro_only",
        },
    )

    assert response.status_code == 403
    assert "publishing_scope" in response.json()["detail"]


def test_domu_editor_can_manage_domu_scoped_news_but_not_common_admin(client):
    app = client.app
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=11, email="domu@example.test", role="domu_editor")

    created = client.post(
        "/api/admin/dom-uchitelya/news/",
        json={
            "title": "House event",
            "slug": "house-event",
            "status": "published",
            "publishing_scope": "both",
            "dom_uchitelya_section": "master-klassy",
            "excerpt": "Event excerpt",
        },
    )
    denied = client.get("/api/admin/news/")

    assert created.status_code == 201
    assert created.json()["publishing_scope"] == "both"
    assert denied.status_code == 403


def test_admin_article_crud_accepts_block_body_taxonomy_fields(client):
    app = client.app
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=12, email="admin@example.test", role="admin")

    created = client.post(
        "/api/admin/news/",
        json={
            "title": "Block material",
            "slug": "block-material",
            "status": "published",
            "lead": "Short lead",
            "body": json.dumps([
                {"id": "b1", "type": "heading", "data": {"text": "Heading", "level": 2}},
                {"id": "b2", "type": "paragraph", "data": {"html": "<strong>Text</strong>"}},
            ], ensure_ascii=False),
            "blocks": [
                {"id": "b1", "type": "heading", "data": {"text": "Heading", "level": 2}},
                {"id": "b2", "type": "paragraph", "data": {"html": "<strong>Text</strong>"}},
            ],
            "cover_image_url": "/static/articles/covers/cover.jpg",
            "is_pinned": True,
            "publishing_scope": "both",
            "tags": ["методика", "иркутск"],
            "methodika_subject": "Математика",
            "dom_uchitelya_section": "master-klassy",
            "noko_section": None,
        },
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["lead"] == "Short lead"
    assert payload["excerpt"] == "Short lead"
    assert json.loads(payload["body"])[0]["type"] == "heading"
    assert payload["blocks"][1]["data"]["html"] == "<strong>Text</strong>"
    assert payload["cover_image_url"].endswith("cover.jpg")
    assert payload["image"] == "/static/articles/covers/cover.jpg"
    assert payload["is_pinned"] is True
    assert payload["methodika_subject"] == "Математика"
    assert payload["dom_uchitelya_section"] == "master-klassy"
