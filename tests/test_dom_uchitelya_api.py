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
            attachments=kwargs.pop("attachments", []),
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
    _add_article_obj(client, title="Both feeds", slug="both-feeds", publishing_scope="both", duplicate_to_main=True)
    _add_article_obj(
        client,
        title="Hub only",
        slug="hub-only",
        publishing_scope="imcro_only",
        hub_kind="konkursy",
        hub_path="kalendar",
        duplicate_to_main=False,
    )

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


def test_public_events_returns_only_marked_articles(client):
    _add_article_obj(client, title="Event article", slug="event-article", duplicate_to_events=True)
    _add_article_obj(client, title="Regular article", slug="regular-article", duplicate_to_events=False)
    response = client.get("/api/events/")
    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["Event article"]


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


def test_admin_sees_all_articles_but_methodist_sees_only_own(client):
    _add_article_obj(client, title="Admin article", slug="admin-article", author_id=20)
    _add_article_obj(client, title="Methodist article", slug="methodist-article", author_id=21)
    _add_article_obj(client, title="Other methodist article", slug="other-methodist-article", author_id=22)

    app = client.app
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, email="admin@example.test", role="admin")
    admin_response = client.get("/api/admin/news/")

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=21, email="methodist@example.test", role="methodist")
    methodist_response = client.get("/api/admin/news/")

    assert admin_response.status_code == 200
    assert {item["title"] for item in admin_response.json()["items"]} == {
        "Admin article",
        "Methodist article",
        "Other methodist article",
    }
    assert methodist_response.status_code == 200
    assert [item["title"] for item in methodist_response.json()["items"]] == ["Methodist article"]


def test_methodist_cannot_update_or_delete_foreign_article(client):
    foreign_id = _add_article_obj(client, title="Foreign article", slug="foreign-article", author_id=22)

    app = client.app
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=21, email="methodist@example.test", role="methodist")

    patch_response = client.patch(f"/api/admin/news/{foreign_id}/", json={"title": "Changed"})
    delete_response = client.delete(f"/api/admin/news/{foreign_id}/")

    assert patch_response.status_code == 403
    assert delete_response.status_code == 403


def test_domu_editor_sees_only_own_domu_articles_and_cannot_manage_foreign(client):
    own_id = _add_article_obj(
        client,
        title="Own domu article",
        slug="own-domu-article",
        author_id=31,
        publishing_scope="both",
        dom_uchitelya_section="master-klassy",
    )
    foreign_id = _add_article_obj(
        client,
        title="Foreign domu article",
        slug="foreign-domu-article",
        author_id=32,
        publishing_scope="dom_uchitelya_only",
        dom_uchitelya_section="master-klassy",
    )
    _add_article_obj(
        client,
        title="Own common article",
        slug="own-common-article",
        author_id=31,
        publishing_scope="imcro_only",
    )

    app = client.app
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=31, email="domu@example.test", role="domu_editor")

    list_response = client.get("/api/admin/dom-uchitelya/news/")
    patch_response = client.patch(f"/api/admin/dom-uchitelya/news/{foreign_id}/", json={"title": "Changed"})
    delete_response = client.delete(f"/api/admin/dom-uchitelya/news/{foreign_id}/")
    wrong_section_response = client.post(
        "/api/admin/dom-uchitelya/news/",
        json={
            "title": "Wrong hub",
            "slug": "wrong-hub",
            "status": "published",
            "publishing_scope": "both",
            "dom_uchitelya_section": "master-klassy",
            "hub_kind": "methodika",
            "hub_path": "metodicheskiy-sovet",
        },
    )

    assert own_id
    assert list_response.status_code == 200
    assert [item["title"] for item in list_response.json()["items"]] == ["Own domu article"]
    assert patch_response.status_code == 403
    assert delete_response.status_code == 403
    assert wrong_section_response.status_code == 403


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
            "attachments": [
                {"url": "/static/articles/attachments/file.pdf", "name": "file.pdf", "type": "PDF"},
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
    assert payload["attachments"][0]["name"] == "file.pdf"
    assert payload["cover_image_url"].endswith("cover.jpg")
    assert payload["image"] == "/static/articles/covers/cover.jpg"
    assert payload["is_pinned"] is True
    assert payload["methodika_subject"] == "Математика"
    assert payload["dom_uchitelya_section"] == "master-klassy"


def test_admin_article_create_makes_duplicate_slug_unique(client):
    app = client.app
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=12, email="admin@example.test", role="admin")

    first = client.post(
        "/api/admin/news/",
        json={
            "title": "Duplicate",
            "slug": "duplicate",
            "status": "draft",
            "lead": "Lead",
            "body": "Body",
            "publishing_scope": "imcro_only",
        },
    )
    second = client.post(
        "/api/admin/news/",
        json={
            "title": "Duplicate",
            "slug": "duplicate",
            "status": "draft",
            "lead": "Lead",
            "body": "Body",
            "publishing_scope": "imcro_only",
        },
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["slug"] == "duplicate"
    assert second.json()["slug"] == "duplicate-2"


def test_pinned_limit_by_section_is_enforced(client):
    app = client.app
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=12, email="admin@example.test", role="admin")
    for index in range(1, 4):
        created = client.post(
            "/api/admin/news/",
            json={
                "title": f"Pinned {index}",
                "slug": f"pinned-{index}",
                "status": "published",
                "lead": "Lead",
                "body": "Body",
                "is_pinned": True,
                "hub_kind": "konkursy",
                "hub_path": "kalendar",
                "publishing_scope": "imcro_only",
            },
        )
        assert created.status_code == 201
    fourth = client.post(
        "/api/admin/news/",
        json={
            "title": "Pinned 4",
            "slug": "pinned-4",
            "status": "published",
            "lead": "Lead",
            "body": "Body",
            "is_pinned": True,
            "hub_kind": "konkursy",
            "hub_path": "kalendar",
            "publishing_scope": "imcro_only",
        },
    )
    assert fourth.status_code == 400


def test_admin_article_attachment_upload_accepts_documents(client):
    app = client.app
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=12, email="admin@example.test", role="admin")

    response = client.post(
        "/api/admin/news/upload-attachment/",
        files={"file": ("plan.pdf", b"%PDF-1.4\ncontent", "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "plan.pdf"
    assert payload["type"] == "PDF"
    assert payload["url"].startswith("/static/articles/attachments/")


def test_admin_articles_alias_routes_match_news_routes(client):
    routes = {
        (route.path, tuple(sorted(route.methods or [])))
        for route in client.app.routes
        if getattr(route, "path", "").startswith("/api/admin/")
    }

    expected_routes = {
        ("/api/admin/news/", ("GET",)),
        ("/api/admin/news/", ("POST",)),
        ("/api/admin/news/{article_id}/", ("PATCH",)),
        ("/api/admin/news/{article_id}/", ("DELETE",)),
        ("/api/admin/news/upload-cover/", ("POST",)),
        ("/api/admin/news/upload-attachment/", ("POST",)),
        ("/api/admin/articles/", ("GET",)),
        ("/api/admin/articles/", ("POST",)),
        ("/api/admin/articles/{article_id}/", ("PATCH",)),
        ("/api/admin/articles/{article_id}/", ("DELETE",)),
        ("/api/admin/articles/upload-cover/", ("POST",)),
        ("/api/admin/articles/upload-attachment/", ("POST",)),
    }

    assert expected_routes <= routes


def test_public_hub_endpoint_filters_by_hub_and_section(client):
    _add_article_obj(
        client,
        title="Конкурсный календарь",
        slug="konkurs-calendar",
        publishing_scope="imcro_only",
        hub_kind="konkursy",
        hub_path="kalendar",
    )
    _add_article_obj(
        client,
        title="НОКО ГИА-9",
        slug="noko-gia9",
        publishing_scope="imcro_only",
        noko_section="gia-9",
    )
    _add_article_obj(
        client,
        title="Методика: математика",
        slug="metod-math",
        publishing_scope="imcro_only",
        methodika_subject="Математика",
    )
    _add_article_obj(
        client,
        title="Методика: совет",
        slug="metod-sovet",
        publishing_scope="imcro_only",
        hub_kind="methodika",
        hub_path="metodicheskiy-sovet",
    )

    konkursy = client.get("/api/hub/news/?hub=konkursy&section=kalendar")
    noko = client.get("/api/hub/news/?hub=noko&section=gia-9")
    methodika = client.get("/api/hub/news/?hub=methodika&subject=Математика")
    methodika_section = client.get("/api/hub/news/?hub=methodika&section=metodicheskiy-sovet")

    assert konkursy.status_code == 200
    assert [item["title"] for item in konkursy.json()["items"]] == ["Конкурсный календарь"]
    assert noko.status_code == 200
    assert [item["title"] for item in noko.json()["items"]] == ["НОКО ГИА-9"]
    assert methodika.status_code == 200
    assert [item["title"] for item in methodika.json()["items"]] == ["Методика: математика"]
    assert methodika_section.status_code == 200
    assert [item["title"] for item in methodika_section.json()["items"]] == ["Методика: совет"]
