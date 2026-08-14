"""
BaseProvider + error taxonomy + capability system.

Capability:
  search | latest | detail | chapters | pages | health

Manager: provider.supports("latest") sebelum memanggil.
Tidak semua provider wajib penuh.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import ChapterInfo, ChapterPages, MangaInfo

CAP_SEARCH = "search"
CAP_LATEST = "latest"
CAP_DETAIL = "detail"
CAP_CHAPTERS = "chapters"
CAP_PAGES = "pages"
CAP_HEALTH = "health"

ALL_CAPABILITIES = frozenset(
    {CAP_SEARCH, CAP_LATEST, CAP_DETAIL, CAP_CHAPTERS, CAP_PAGES, CAP_HEALTH}
)


class ProviderError(Exception):
    kind: str = "provider"
    retryable: bool = False
    skip_other_providers: bool = False
    degrade_provider: bool = False
    cooldown_sec: float = 0.0

    def __init__(
        self,
        provider: str,
        message: str,
        cause: Exception | None = None,
        *,
        status: int | None = None,
    ):
        self.provider = provider
        self.cause = cause
        self.status = status
        super().__init__(f"[{provider}] {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "provider": self.provider,
            "message": str(self),
            "status": self.status,
            "retryable": self.retryable,
            "skip_other_providers": self.skip_other_providers,
            "degrade_provider": self.degrade_provider,
            "cooldown_sec": self.cooldown_sec,
        }


class NetworkError(ProviderError):
    kind = "network"
    retryable = True
    degrade_provider = True
    cooldown_sec = 15.0


class TimeoutError(ProviderError):
    kind = "timeout"
    retryable = True
    degrade_provider = True
    cooldown_sec = 20.0


class HTTPError(ProviderError):
    kind = "http"
    retryable = False

    def __init__(self, provider: str, message: str, status: int, cause: Exception | None = None):
        super().__init__(provider, message, cause, status=status)
        if status == 404:
            self.kind = "http_404"
            self.skip_other_providers = False
            self.retryable = False
        elif status == 403:
            self.kind = "http_403"
            self.degrade_provider = True
            self.cooldown_sec = 120.0
        elif status == 429:
            self.kind = "http_429"
            self.degrade_provider = True
            self.retryable = True
            self.cooldown_sec = 180.0
        elif status >= 500:
            self.kind = "http_5xx"
            self.retryable = True
            self.degrade_provider = True
            self.cooldown_sec = 45.0
        else:
            self.kind = f"http_{status}"
            self.degrade_provider = status >= 400


class ParseError(ProviderError):
    kind = "parse"
    retryable = False
    degrade_provider = True
    cooldown_sec = 60.0


class EmptyResult(ProviderError):
    kind = "empty"
    retryable = False
    skip_other_providers = False
    degrade_provider = False


class ProviderBlocked(ProviderError):
    kind = "blocked"
    retryable = False
    degrade_provider = True
    cooldown_sec = 300.0


def classify_exception(provider: str, exc: Exception) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    name = type(exc).__name__.lower()
    msg = str(exc) or name
    low = msg.lower()
    if "timeout" in name or "timeout" in low or "timed out" in low:
        return TimeoutError(provider, msg, exc)
    if any(x in low for x in ("cloudflare", "just a moment", "cf-ray", "attention required", "captcha")):
        return ProviderBlocked(provider, msg, exc)
    if any(x in low for x in ("connection", "network", "dns", "name resolution", "refused", "reset")):
        return NetworkError(provider, msg, exc)
    if "429" in low or "rate limit" in low or "too many" in low:
        return HTTPError(provider, msg, 429, exc)
    if "403" in low or "forbidden" in low:
        return HTTPError(provider, msg, 403, exc)
    if "404" in low or "not found" in low:
        return HTTPError(provider, msg, 404, exc)
    if any(x in low for x in ("500", "502", "503", "504", "bad gateway", "unavailable")):
        code = 503
        for c in (500, 502, 503, 504):
            if str(c) in low:
                code = c
                break
        return HTTPError(provider, msg, code, exc)
    if any(x in low for x in ("json", "parse", "decode", "expecting", "html instead")):
        return ParseError(provider, msg, exc)
    return ProviderError(provider, msg, exc)


class BaseProvider(ABC):
    """
    Adapter provider.

    Set `capabilities` di subclass bila tidak full-stack.
    Manager memanggil supports("latest") sebelum get_latest, dll.
    """

    name: str = "base"
    priority: int = 100
    capabilities: frozenset[str] = ALL_CAPABILITIES

    def supports(self, capability: str) -> bool:
        return capability in (self.capabilities or ALL_CAPABILITIES)

    def capability_map(self) -> dict[str, bool]:
        return {c: self.supports(c) for c in sorted(ALL_CAPABILITIES)}

    @abstractmethod
    def search(self, keyword: str, limit: int = 20) -> list[MangaInfo]:
        ...

    @abstractmethod
    def get_latest(self, limit: int = 20, page: int = 1) -> list[MangaInfo]:
        ...

    @abstractmethod
    def get_manga(self, source_slug: str) -> MangaInfo | None:
        ...

    @abstractmethod
    def get_chapters(self, source_slug: str) -> list[ChapterInfo]:
        ...

    @abstractmethod
    def get_pages(self, chapter: ChapterInfo) -> ChapterPages:
        ...

    def health(self) -> dict[str, Any]:
        if not self.supports(CAP_HEALTH):
            return {"provider": self.name, "ok": False, "error": "no health capability"}
        try:
            sample = 0
            if self.supports(CAP_LATEST):
                items = self.get_latest(page=1, limit=1)
                sample = len(items)
            return {
                "provider": self.name,
                "ok": True,
                "sample": sample,
                "capabilities": self.capability_map(),
            }
        except Exception as e:
            err = classify_exception(self.name, e)
            return {
                "provider": self.name,
                "ok": False,
                "error": err.to_dict(),
                "capabilities": self.capability_map(),
            }
