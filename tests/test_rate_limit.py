from fastapi import FastAPI
from fastapi.testclient import TestClient

from rate_limit import RateLimitMiddleware, RateLimitRule, find_rate_limit_rule


def _limited_client(rule: RateLimitRule) -> TestClient:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, rules=(rule,), enabled=True)

    @app.post("/auth/login")
    def login():
        return {"ok": True}

    @app.get("/api/news/")
    def news():
        return {"items": []}

    return TestClient(app)


def test_sensitive_route_returns_429_after_limit_is_exceeded():
    client = _limited_client(
        RateLimitRule(
            name="auth",
            limit=2,
            window_seconds=60,
            methods=("POST",),
            exact_paths=("/auth/login",),
        )
    )

    assert client.post("/auth/login").status_code == 200
    assert client.post("/auth/login").status_code == 200

    blocked = client.post("/auth/login")

    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]
    assert "detail" in blocked.json()


def test_unmatched_public_get_route_is_not_limited():
    client = _limited_client(
        RateLimitRule(
            name="auth",
            limit=1,
            window_seconds=60,
            methods=("POST",),
            exact_paths=("/auth/login",),
        )
    )

    assert client.get("/api/news/").status_code == 200
    assert client.get("/api/news/").status_code == 200
    assert client.get("/api/news/").status_code == 200


def test_default_rules_cover_sensitive_api_only():
    assert find_rate_limit_rule("POST", "/auth/login").name == "auth"
    assert find_rate_limit_rule("POST", "/auth/register").name == "auth"
    assert find_rate_limit_rule("POST", "/api/tpmpk/zapis/").name == "tpmpk_appointment"
    assert find_rate_limit_rule("POST", "/api/tpmpk/slot-locks/").name == "tpmpk_slot_locks"
    assert find_rate_limit_rule("DELETE", "/api/tpmpk/slot-locks/").name == "tpmpk_slot_locks"
    assert find_rate_limit_rule("POST", "/certificates/generate").name == "certificate_generation"
    assert find_rate_limit_rule("POST", "/api/admin/articles/upload-cover/").name == "uploads"
    assert find_rate_limit_rule("PUT", "/users/roles/1/permissions/").name == "admin_mutation"

    assert find_rate_limit_rule("GET", "/api/news/") is None
    assert find_rate_limit_rule("GET", "/api/search/") is None
    assert find_rate_limit_rule("GET", "/static/app.js") is None
