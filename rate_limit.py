from __future__ import annotations

import math
import os
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


SENSITIVE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


@dataclass(frozen=True)
class RateLimitRule:
    name: str
    limit: int
    window_seconds: int
    methods: tuple[str, ...] | None = None
    exact_paths: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()

    def matches(self, method: str, path: str) -> bool:
        if self.methods is not None and method.upper() not in {item.upper() for item in self.methods}:
            return False

        normalized_path = _normalize_path(path)
        if any(_same_path(normalized_path, exact_path) for exact_path in self.exact_paths):
            return True
        return any(normalized_path.startswith(_normalize_path(prefix)) for prefix in self.prefixes)


DEFAULT_RATE_LIMIT_RULES: tuple[RateLimitRule, ...] = (
    RateLimitRule(
        name="auth",
        limit=10,
        window_seconds=60,
        methods=("POST",),
        exact_paths=("/auth/login", "/auth/register"),
    ),
    RateLimitRule(
        name="tpmpk_appointment",
        limit=5,
        window_seconds=10 * 60,
        methods=("POST",),
        exact_paths=("/api/tpmpk/zapis",),
    ),
    RateLimitRule(
        name="tpmpk_slot_locks",
        limit=30,
        window_seconds=60,
        methods=("POST", "DELETE"),
        exact_paths=("/api/tpmpk/slot-locks",),
    ),
    RateLimitRule(
        name="uploads",
        limit=20,
        window_seconds=60,
        methods=("POST",),
        prefixes=(
            "/certificates/upload-",
            "/api/admin/articles/upload-",
            "/api/admin/news/upload-",
            "/api/admin/dom-uchitelya/news/upload-",
        ),
    ),
    RateLimitRule(
        name="certificate_generation",
        limit=12,
        window_seconds=10 * 60,
        methods=("POST",),
        exact_paths=(
            "/certificates/batch",
            "/certificates/excel/inspect",
            "/certificates/generate",
            "/certificates/manual",
        ),
    ),
    RateLimitRule(
        name="admin_mutation",
        limit=120,
        window_seconds=60,
        methods=SENSITIVE_METHODS,
        prefixes=(
            "/api/admin/",
            "/api/tpmpk/admin/",
            "/certificates/",
            "/users/",
        ),
    ),
)


def find_rate_limit_rule(
    method: str,
    path: str,
    rules: Iterable[RateLimitRule] = DEFAULT_RATE_LIMIT_RULES,
) -> RateLimitRule | None:
    for rule in rules:
        if rule.matches(method, path):
            return rule
    return None


def rate_limit_enabled_from_env() -> bool:
    value = os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


class InMemoryRateLimiter:
    def __init__(self, rules: Iterable[RateLimitRule] = DEFAULT_RATE_LIMIT_RULES):
        self.rules = tuple(rules)
        self._attempts: dict[str, deque[float]] = {}
        self._lock = Lock()
        self._max_window = max((rule.window_seconds for rule in self.rules), default=60)
        self._next_cleanup_at = 0.0

    def hit(self, rule: RateLimitRule, identity: str, now: float | None = None) -> tuple[bool, int, int]:
        now = time.monotonic() if now is None else now
        key = f"{rule.name}:{identity}"
        cutoff = now - rule.window_seconds

        with self._lock:
            if now >= self._next_cleanup_at:
                self._cleanup(now)

            attempts = self._attempts.setdefault(key, deque())
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()

            if len(attempts) >= rule.limit:
                retry_after = max(1, math.ceil(attempts[0] + rule.window_seconds - now))
                return False, retry_after, 0

            attempts.append(now)
            remaining = max(0, rule.limit - len(attempts))
            return True, 0, remaining

    def _cleanup(self, now: float) -> None:
        cutoff = now - self._max_window
        for key, attempts in list(self._attempts.items()):
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if not attempts:
                del self._attempts[key]
        self._next_cleanup_at = now + 60


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        rules: Iterable[RateLimitRule] = DEFAULT_RATE_LIMIT_RULES,
        enabled: bool | None = None,
        limiter: InMemoryRateLimiter | None = None,
    ):
        super().__init__(app)
        self.rules = tuple(rules)
        self.enabled = rate_limit_enabled_from_env() if enabled is None else enabled
        self.limiter = limiter or InMemoryRateLimiter(self.rules)

    async def dispatch(self, request: Request, call_next):
        if not self.enabled or request.method.upper() in {"OPTIONS", "HEAD"}:
            return await call_next(request)

        rule = find_rate_limit_rule(request.method, request.url.path, self.rules)
        if rule is None:
            return await call_next(request)

        allowed, retry_after, remaining = self.limiter.hit(rule, _client_identity(request))
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Слишком много запросов. Попробуйте позже."},
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(rule.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Rule": rule.name,
                },
            )

        response = await call_next(request)
        response.headers.setdefault("X-RateLimit-Limit", str(rule.limit))
        response.headers.setdefault("X-RateLimit-Remaining", str(remaining))
        response.headers.setdefault("X-RateLimit-Rule", rule.name)
        return response


def _client_identity(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or "unknown"

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _normalize_path(path: str) -> str:
    clean = "/" + str(path or "").strip().lstrip("/")
    return clean.rstrip("/") or "/"


def _same_path(left: str, right: str) -> bool:
    return _normalize_path(left) == _normalize_path(right)
