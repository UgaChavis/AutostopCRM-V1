from __future__ import annotations

import html
import ipaddress
import json
import math
import os
import re
import shutil
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx

_RESULT_BLOCK_PATTERN = re.compile(r'<div class="result(?:__body)?[^"]*".*?</div>\s*</div>', re.S)
_RESULT_LINK_PATTERN = re.compile(
    r'<a rel="nofollow" class="result__a" href="(?P<href>[^"]+)">(?P<title>.*?)</a>',
    re.S,
)
_RESULT_SNIPPET_PATTERN = re.compile(
    r'<a class="result__snippet" href="[^"]+">(?P<snippet>.*?)</a>|<div class="result__snippet">(?P<snippet_alt>.*?)</div>',
    re.S,
)
_TAG_PATTERN = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_PATTERN = re.compile(r"<(?:script|style)\b.*?>.*?</(?:script|style)>", re.S | re.I)
_MULTISPACE_PATTERN = re.compile(r"\s+")
_NUMERIC_ARTICLE_QUERY_PATTERN = re.compile(r"(?<!\w)[0-9]{6,14}(?!\w)")
_DEFAULT_SEARCH_LIMIT = 5
_MAX_SEARCH_LIMIT = 10
_DEFAULT_PAGE_EXCERPT_CHARS = 2500
_MAX_PAGE_EXCERPT_CHARS = 10_000
_DEFAULT_TIMEOUT_SECONDS = 12.0
_DEFAULT_BROWSER_WAIT_MS = 750
_MAX_BROWSER_WAIT_MS = 5_000
_MAX_BROWSER_LINKS = 30
_MAX_SEARCH_RESPONSE_BYTES = 1_500_000
_MAX_PAGE_RESPONSE_BYTES = 2_000_000
_MAX_REDIRECTS = 5
_MAX_BROWSER_REQUESTS = 48
_BROWSER_EGRESS_ISOLATION_REQUIRED_ERROR = "browser_egress_isolation_required"
_SEARCH_PROVIDER_ORDER = ("brave", "tavily", "google_cse", "searxng", "marginalia", "duckduckgo")
_SEARCH_PROVIDER_ALIASES = {
    "brave_search": "brave",
    "brave": "brave",
    "tavily": "tavily",
    "google": "google_cse",
    "google_cse": "google_cse",
    "google_custom_search": "google_cse",
    "searx": "searxng",
    "searxng": "searxng",
    "searx_ng": "searxng",
    "marginalia": "marginalia",
    "marginalia_search": "marginalia",
    "duckduckgo": "duckduckgo",
    "ddg": "duckduckgo",
}
_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
_GOOGLE_CSE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"
_MARGINALIA_SEARCH_URL = "https://api2.marginalia-search.com/search"
_BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".test", ".invalid")
_BLOCKED_BROWSER_RESOURCE_TYPES = frozenset(
    {"eventsource", "font", "image", "manifest", "media", "websocket"}
)
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 AutoStopCRM/1.0"
)
_ACCESS_FLAG_PATTERNS = (
    ("captcha_required", re.compile(r"\b(?:captcha|recaptcha|hcaptcha)\b|капч", re.I)),
    (
        "js_challenge",
        re.compile(
            r"ngenix_jscc|cf-chl|challenge-platform|js challenge|"
            r"checking your browser|проверка браузера|пройти проверку",
            re.I,
        ),
    ),
    (
        "login_required",
        re.compile(r"\b(?:login|sign in|sign-in|authorization)\b|войти|авторизац", re.I),
    ),
    ("ip_blocked", re.compile(r"ip.*blocked|blocked.*ip|доступ.*ip|проблема с ip", re.I)),
    (
        "access_denied",
        re.compile(
            r"access denied|forbidden|доступ запрещен|доступ ограничен|verify you are human",
            re.I,
        ),
    ),
    (
        "js_required",
        re.compile(
            r"enable javascript|javascript is disabled|включите.*javascript|выключен javascript",
            re.I,
        ),
    ),
    ("rate_limited", re.compile(r"too many requests|rate limit|429|слишком много запрос", re.I)),
)
_HUMAN_REQUIRED_FLAGS = frozenset(
    {"captcha_required", "login_required", "ip_blocked", "access_denied", "js_challenge"}
)
_VIN_LIKE_QUERY_TOKEN_PATTERN = re.compile(
    r"(?<![A-HJ-NPR-Z0-9])(?:[A-HJ-NPR-Z0-9][._/\\-]?){17}(?![A-HJ-NPR-Z0-9])",
    re.IGNORECASE,
)
_SPACED_VIN_QUERY_TOKEN_PATTERN = re.compile(
    r"(?<![A-HJ-NPR-Z0-9])(?:[A-HJ-NPR-Z0-9]{2,8}[ \t]+){2,6}"
    r"[A-HJ-NPR-Z0-9]{2,8}(?![A-HJ-NPR-Z0-9])",
    re.IGNORECASE,
)
_PHONE_QUERY_TOKEN_PATTERN = re.compile(
    r"(?<!\d)(?:\+7|8)\s*(?:\(\s*\d{3}\s*\)|\d{3})\s*[-. ]?\s*\d{3}\s*[-. ]?\s*\d{2}\s*[-. ]?\s*\d{2}(?!\d)"
)
_EMAIL_QUERY_TOKEN_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b", re.IGNORECASE)
_SENSITIVE_QUERY_FIELD_PATTERN = re.compile(
    r"""(?ix)
    \b(?:
        access[_ -]?token|api[_ -]?key|authorization|bearer|client(?:[_ -]?id)?|
        contact|customer(?:[_ -]?id)?|email|e-mail|owner|password|phone|secret|telegram|
        token|buyer|клиент|заказчик|покупатель|контакт|телефон|почта
    )\s*[:=]\s*(?:\"[^\"]{0,256}\"|'[^']{0,256}'|[^,;|\n]{1,256})
    """
)


class InternetToolError(RuntimeError):
    pass


def redact_public_vin_text(value: str) -> tuple[str, bool]:
    """Redact VINs without swallowing a brand followed by a part number."""

    text, direct_count = _VIN_LIKE_QUERY_TOKEN_PATTERN.subn("[vin-redacted]", value)
    spaced_count = 0

    def redact_spaced(match: re.Match[str]) -> str:
        nonlocal spaced_count
        compact = re.sub(r"\s+", "", match.group())
        if len(compact) == 17 and any(char.isdigit() for char in compact):
            spaced_count += 1
            return "[vin-redacted]"
        return match.group()

    text = _SPACED_VIN_QUERY_TOKEN_PATTERN.sub(redact_spaced, text)
    return text, bool(direct_count or spaced_count)


def sanitize_public_search_query(value: Any) -> str:
    """Remove identifiers and credentials before a query leaves the CRM boundary."""

    text = str(value or "").strip()
    for _ in range(3):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    text = _SENSITIVE_QUERY_FIELD_PATTERN.sub(" ", text)
    text = _EMAIL_QUERY_TOKEN_PATTERN.sub(" ", text)
    text = _PHONE_QUERY_TOKEN_PATTERN.sub(" ", text)
    text = redact_public_vin_text(text)[0].replace("[vin-redacted]", " ")
    text = _MULTISPACE_PATTERN.sub(" ", text).strip()
    if not text:
        raise InternetToolError("public search query is required after redaction")
    return text


class _PublicBrowserRequestGuard:
    """Defense in depth for a future egress-isolated browser renderer.

    Playwright exposes request routing but no stable per-request IP pinning API.
    A DNS check before ``route.continue_`` is not a network boundary and cannot
    prevent rebinding; this process therefore does not enable browser navigation.
    """

    def __init__(self, client: DuckDuckGoSearchClient) -> None:
        self._client = client
        self._request_count = 0

    def __call__(self, route: Any, request: Any) -> None:
        self._request_count += 1
        resource_type = str(getattr(request, "resource_type", "") or "").casefold()
        method = str(getattr(request, "method", "") or "").upper()
        if (
            self._request_count > _MAX_BROWSER_REQUESTS
            or resource_type in _BLOCKED_BROWSER_RESOURCE_TYPES
            or method not in {"GET", "HEAD"}
        ):
            route.abort()
            return
        try:
            self._client._validated_public_http_url(str(getattr(request, "url", "") or ""))
        except InternetToolError:
            route.abort()
            return
        route.continue_()


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    domain: str
    provider: str = "duckduckgo"

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "domain": self.domain,
            "provider": self.provider,
        }


class DuckDuckGoSearchClient:
    def __init__(self, *, timeout_seconds: float = 12.0) -> None:
        self._timeout_seconds = _normalize_seconds(
            timeout_seconds, default=_DEFAULT_TIMEOUT_SECONDS, minimum=1.0, maximum=60.0
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        allowed_domains: list[str] | None = None,
    ) -> list[SearchResult]:
        query_text = sanitize_public_search_query(query)
        normalized_limit = _normalize_int(
            limit, default=_DEFAULT_SEARCH_LIMIT, maximum=_MAX_SEARCH_LIMIT
        )
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query_text)}"
        try:
            with httpx.Client(
                timeout=self._timeout_seconds,
                headers={"User-Agent": "Mozilla/5.0 AutoStopCRM/1.0"},
                trust_env=False,
            ) as client:
                html_text = self._fetch_limited_text(client, url, _MAX_SEARCH_RESPONSE_BYTES)
        except httpx.HTTPError as exc:
            raise InternetToolError(f"Web search failed: {exc}") from exc
        return self._parse_results(
            html_text, limit=normalized_limit, allowed_domains=allowed_domains
        )

    def search_multi(
        self,
        query: str,
        *,
        limit: int = 5,
        allowed_domains: list[str] | None = None,
        providers: list[str] | None = None,
    ) -> dict[str, Any]:
        query_text = sanitize_public_search_query(query)
        normalized_limit = _normalize_int(
            limit, default=_DEFAULT_SEARCH_LIMIT, maximum=_MAX_SEARCH_LIMIT
        )
        allowed = _normalize_domain_list(allowed_domains)
        provider_order = _normalize_provider_order(providers)
        article_patterns = tuple(
            re.compile(r"(?<![0-9])" + r"[\s._/\-]?".join(article) + r"(?![0-9])")
            for article in dict.fromkeys(_NUMERIC_ARTICLE_QUERY_PATTERN.findall(query_text))
        )
        attempts: list[dict[str, Any]] = []
        results: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for provider in provider_order:
            batch, attempt = self._run_search_provider(
                provider,
                query=query_text,
                limit=normalized_limit,
                allowed_domains=allowed,
            )
            added_count = 0
            article_filtered_count = 0
            for result in batch:
                if article_patterns and not any(
                    pattern.search(unquote(" ".join((result.title, result.snippet, result.url))))
                    for pattern in article_patterns
                ):
                    article_filtered_count += 1
                    continue
                dedupe_key = _canonical_result_url(result.url)
                if not dedupe_key or dedupe_key in seen_urls:
                    continue
                seen_urls.add(dedupe_key)
                results.append(result.to_dict())
                added_count += 1
                if len(results) >= normalized_limit:
                    break
            attempt["result_count"] = len(batch)
            attempt["added_count"] = added_count
            if article_patterns:
                attempt["article_filtered_count"] = article_filtered_count
            attempts.append(attempt)
            if len(results) >= normalized_limit:
                break

        return {
            "query": query_text,
            "results": results[:normalized_limit],
            "provider_order": provider_order,
            "providers": attempts,
            "fallback_used": any(
                item.get("provider") == "duckduckgo" and item.get("status") == "success"
                for item in attempts
            ),
        }

    def fetch_page_excerpt(self, url: str, *, max_chars: int = 2500) -> dict[str, Any]:
        normalized_url = str(url or "").strip()
        if not normalized_url:
            raise InternetToolError("url is required")
        normalized_url = self._validated_public_http_url(normalized_url)
        normalized_max_chars = _normalize_int(
            max_chars,
            default=_DEFAULT_PAGE_EXCERPT_CHARS,
            maximum=_MAX_PAGE_EXCERPT_CHARS,
        )
        try:
            with httpx.Client(
                timeout=self._timeout_seconds,
                headers={"User-Agent": "Mozilla/5.0 AutoStopCRM/1.0"},
                trust_env=False,
            ) as client:
                response_url, html_text = self._fetch_limited_text_with_url(
                    client, normalized_url, _MAX_PAGE_RESPONSE_BYTES
                )
        except httpx.HTTPError as exc:
            raise InternetToolError(f"Page fetch failed: {exc}") from exc
        text = self._clean_html_text(html_text)
        access_flags = _detect_access_flags(" ".join((response_url, text[:5000])))
        return {
            "ok": True,
            "url": normalized_url,
            "final_url": response_url,
            "domain": self._url_hostname(response_url),
            "excerpt": text[:normalized_max_chars],
            "format": "plain_text",
            "access_flags": access_flags,
            "requires_human": any(flag in _HUMAN_REQUIRED_FLAGS for flag in access_flags),
            "engine": "httpx_html",
            "mode": "http_excerpt",
            "extractors": [_provider_attempt("httpx_html", "success")],
            "fallback_used": False,
        }

    def fetch_page_browser(
        self, url: str, *, max_chars: int = 2500, wait_ms: int = _DEFAULT_BROWSER_WAIT_MS
    ) -> dict[str, Any]:
        normalized_url = str(url or "").strip()
        if not normalized_url:
            raise InternetToolError("url is required")
        # Keep syntactic and literal-private-target validation, but do not resolve
        # or navigate.  Chromium has no stable per-request IP-pinning API, so an
        # application-level DNS preflight cannot safely authorize this sink.
        normalized_url = self._validated_public_http_url(normalized_url, resolve_dns=False)
        if not _browser_egress_isolation_verified():
            return self._browser_error_payload(
                normalized_url,
                error=_BROWSER_EGRESS_ISOLATION_REQUIRED_ERROR,
                message=(
                    "Browser rendering is disabled until it runs behind a verified "
                    "private-range egress isolation boundary."
                ),
                access_flags=["browser_egress_unverified"],
            )
        normalized_max_chars = _normalize_int(
            max_chars,
            default=_DEFAULT_PAGE_EXCERPT_CHARS,
            maximum=_MAX_PAGE_EXCERPT_CHARS,
        )
        normalized_wait_ms = _normalize_int(
            wait_ms,
            default=_DEFAULT_BROWSER_WAIT_MS,
            minimum=0,
            maximum=_MAX_BROWSER_WAIT_MS,
        )
        sync_playwright = _load_sync_playwright()
        if sync_playwright is None:
            return self._browser_error_payload(
                normalized_url,
                error="playwright_missing",
                message="Playwright is not installed in the runtime.",
                access_flags=["browser_unavailable"],
            )

        try:
            with sync_playwright() as playwright:
                browser = self._launch_chromium(playwright)
                context = None
                try:
                    context = browser.new_context(
                        user_agent=_BROWSER_USER_AGENT,
                        locale="ru-RU",
                        viewport={"width": 1365, "height": 900},
                        ignore_https_errors=False,
                    )
                    context.route("**/*", _PublicBrowserRequestGuard(self))
                    page = context.new_page()
                    response = page.goto(
                        normalized_url,
                        wait_until="domcontentloaded",
                        timeout=int(self._timeout_seconds * 1000),
                    )
                    if normalized_wait_ms:
                        page.wait_for_timeout(normalized_wait_ms)
                    try:
                        page.wait_for_load_state("networkidle", timeout=1500)
                    except Exception:
                        pass
                    final_url = self._validated_public_http_url(str(page.url or normalized_url))
                    title = self._browser_title(page)
                    text = self._browser_text(page)
                    if not text:
                        text = self._clean_html_text(page.content())
                    links = self._browser_links(page, base_url=final_url)
                    status_code = int(getattr(response, "status", 0) or 0)
                    access_flags = _detect_access_flags(
                        " ".join((title, text[:5000], final_url)), status_code=status_code
                    )
                    return {
                        "ok": True,
                        "url": normalized_url,
                        "final_url": final_url,
                        "domain": self._url_hostname(final_url),
                        "title": title,
                        "status_code": status_code,
                        "excerpt": text[:normalized_max_chars],
                        "links": links,
                        "access_flags": access_flags,
                        "requires_human": any(
                            flag in _HUMAN_REQUIRED_FLAGS for flag in access_flags
                        ),
                        "engine": "playwright_chromium",
                        "mode": "browser",
                    }
                finally:
                    if context is not None:
                        context.close()
                    browser.close()
        except InternetToolError:
            raise
        except Exception as exc:
            return self._browser_error_payload(
                normalized_url,
                error="browser_error",
                message=_safe_error_message(exc),
                access_flags=["browser_error"],
            )

    def _fetch_limited_text(self, client: httpx.Client, url: str, max_bytes: int) -> str:
        return self._fetch_limited_text_with_url(client, url, max_bytes)[1]

    def _fetch_limited_text_with_url(
        self, client: httpx.Client, url: str, max_bytes: int
    ) -> tuple[str, str]:
        current_url = self._validated_public_http_url(url, resolve_dns=False)
        for _ in range(_MAX_REDIRECTS + 1):
            with self._stream_public_request(client, "GET", current_url) as response:
                status_code = int(getattr(response, "status_code", 200) or 200)
                if 300 <= status_code < 400:
                    location = str(getattr(response, "headers", {}).get("location", "") or "")
                    if not location:
                        raise InternetToolError("Web redirect is missing a Location header.")
                    current_url = self._validated_public_http_url(
                        urljoin(current_url, location), resolve_dns=False
                    )
                    continue
                response.raise_for_status()
                encoding = response.encoding or "utf-8"
                text = self._read_limited_response_bytes(response, max_bytes).decode(
                    encoding, errors="replace"
                )
                return current_url, text
        raise InternetToolError("Web redirect chain is too long.")

    def _read_limited_response_bytes(self, response: Any, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes(chunk_size=max_bytes + 1):
            total += len(chunk)
            if total > max_bytes:
                raise InternetToolError("Web response is too large.")
            chunks.append(chunk)
        return b"".join(chunks)

    @contextmanager
    def _stream_public_request(
        self, client: Any, method: str, url: str, **request_kwargs: Any
    ) -> Any:
        normalized_url = self._validated_public_http_url(url, resolve_dns=False)
        parsed = urlparse(normalized_url)
        host = str(parsed.hostname or "").strip().casefold().rstrip(".")
        port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
        addresses = self._resolve_public_host(host, port)

        if not hasattr(client, "build_request") or not hasattr(client, "send"):
            with client.stream(
                method, normalized_url, follow_redirects=False, **request_kwargs
            ) as response:
                yield response
            return

        address = addresses[0]
        pinned_host = f"[{address}]" if ":" in address else address
        explicit_port = parsed.port
        pinned_netloc = f"{pinned_host}:{explicit_port}" if explicit_port else pinned_host
        pinned_url = parsed._replace(netloc=pinned_netloc).geturl()
        headers = dict(request_kwargs.pop("headers", {}) or {})
        headers["Host"] = _http_host_header(host, explicit_port, parsed.scheme.casefold())
        request = client.build_request(method, pinned_url, headers=headers, **request_kwargs)
        request.extensions["sni_hostname"] = host
        response = client.send(request, stream=True, follow_redirects=False)
        try:
            yield response
        finally:
            response.close()

    def _fetch_limited_json(
        self,
        client: Any,
        method: str,
        url: str,
        *,
        max_bytes: int,
        public: bool = True,
        **request_kwargs: Any,
    ) -> Any:
        if public:
            response_context = self._stream_public_request(client, method, url, **request_kwargs)
        else:
            response_context = client.stream(method, url, follow_redirects=False, **request_kwargs)
        with response_context as response:
            response.raise_for_status()
            encoding = response.encoding or "utf-8"
            payload_text = self._read_limited_response_bytes(response, max_bytes).decode(
                encoding, errors="replace"
            )
        try:
            return json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise InternetToolError("Web response was not valid JSON.") from exc

    def _validated_public_http_url(self, url: str, *, resolve_dns: bool = True) -> str:
        try:
            parsed = urlparse(url)
            port = parsed.port
        except ValueError as exc:
            raise InternetToolError("Only public HTTP(S) URLs are supported.") from exc
        scheme = str(parsed.scheme or "").casefold()
        host = str(parsed.hostname or "").strip().casefold().rstrip(".")
        if (
            scheme not in {"http", "https"}
            or not parsed.netloc
            or not host
            or port == 0
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise InternetToolError("Only public HTTP(S) URLs are supported.")
        resolved_port = port or (443 if scheme == "https" else 80)
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES) or "." not in host:
                raise InternetToolError("Local or private URLs are not supported.")
            if resolve_dns:
                self._resolve_public_host(host, resolved_port)
            return url
        if not address.is_global:
            raise InternetToolError("Local or private URLs are not supported.")
        return url

    def _resolve_public_host(self, host: str, port: int) -> list[str]:
        try:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (OSError, ValueError) as exc:
            raise InternetToolError("Public host could not be resolved.") from exc
        addresses: list[str] = []
        for record in records:
            try:
                address = ipaddress.ip_address(str(record[4][0]))
            except (IndexError, ValueError):
                raise InternetToolError("Public host resolved to an invalid address.") from None
            if not address.is_global:
                raise InternetToolError("Local or private URLs are not supported.")
            normalized = str(address)
            if normalized not in addresses:
                addresses.append(normalized)
        if not addresses:
            raise InternetToolError("Public host could not be resolved.")
        return addresses

    def _parse_results(
        self,
        html_text: str,
        *,
        limit: int,
        allowed_domains: list[str] | None,
    ) -> list[SearchResult]:
        normalized_limit = _normalize_int(
            limit, default=_DEFAULT_SEARCH_LIMIT, maximum=_MAX_SEARCH_LIMIT
        )
        allowed = [item.casefold() for item in _normalize_domain_list(allowed_domains)]
        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for block in _RESULT_BLOCK_PATTERN.findall(html_text):
            link_match = _RESULT_LINK_PATTERN.search(block)
            if not link_match:
                continue
            snippet_match = _RESULT_SNIPPET_PATTERN.search(block)
            resolved_url = self._resolve_duckduckgo_url(link_match.group("href"))
            if not resolved_url or resolved_url in seen_urls:
                continue
            try:
                resolved_url = self._validated_public_http_url(resolved_url, resolve_dns=False)
            except InternetToolError:
                continue
            domain = self._url_hostname(resolved_url)
            if allowed and not _domain_allowed(domain, allowed):
                continue
            seen_urls.add(resolved_url)
            results.append(
                SearchResult(
                    title=self._clean_html_text(link_match.group("title")),
                    url=resolved_url,
                    snippet=self._clean_html_text(
                        (snippet_match.group("snippet") if snippet_match else "")
                        or (snippet_match.group("snippet_alt") if snippet_match else "")
                    ),
                    domain=domain,
                )
            )
            if len(results) >= normalized_limit:
                break
        return results

    def _run_search_provider(
        self,
        provider: str,
        *,
        query: str,
        limit: int,
        allowed_domains: list[str],
    ) -> tuple[list[SearchResult], dict[str, Any]]:
        if provider == "brave":
            api_key = _first_env("BRAVE_SEARCH_API_KEY", "BRAVE_API_KEY")
            if not api_key:
                return [], _provider_attempt(provider, "skipped", reason="missing_api_key")
            return self._try_json_search_provider(
                provider,
                lambda: self._search_brave(
                    query=query,
                    limit=limit,
                    allowed_domains=allowed_domains,
                    api_key=api_key,
                ),
            )
        if provider == "tavily":
            api_key = _first_env("TAVILY_API_KEY")
            if not api_key:
                return [], _provider_attempt(provider, "skipped", reason="missing_api_key")
            return self._try_json_search_provider(
                provider,
                lambda: self._search_tavily(
                    query=query,
                    limit=limit,
                    allowed_domains=allowed_domains,
                    api_key=api_key,
                ),
            )
        if provider == "google_cse":
            api_key = _first_env("GOOGLE_CUSTOM_SEARCH_API_KEY", "GOOGLE_CSE_API_KEY")
            cx = _first_env("GOOGLE_CUSTOM_SEARCH_CX", "GOOGLE_CSE_CX", "GOOGLE_CSE_ID")
            if not api_key or not cx:
                missing = []
                if not api_key:
                    missing.append("api_key")
                if not cx:
                    missing.append("cx")
                return [], _provider_attempt(
                    provider,
                    "skipped",
                    reason="missing_" + "_".join(missing),
                )
            return self._try_json_search_provider(
                provider,
                lambda: self._search_google_cse(
                    query=query,
                    limit=limit,
                    allowed_domains=allowed_domains,
                    api_key=api_key,
                    cx=cx,
                ),
            )
        if provider == "searxng":
            base_url = _first_env("AUTOSTOP_SEARXNG_BASE_URL", "SEARXNG_BASE_URL")
            if not base_url:
                return [], _provider_attempt(provider, "skipped", reason="missing_base_url")
            return self._try_json_search_provider(
                provider,
                lambda: self._search_searxng(
                    query=query,
                    limit=limit,
                    allowed_domains=allowed_domains,
                    base_url=base_url,
                ),
            )
        if provider == "marginalia":
            api_key = _first_env("AUTOSTOP_MARGINALIA_API_KEY", "MARGINALIA_API_KEY")
            enabled = api_key or _truthy(_first_env("AUTOSTOP_MARGINALIA_ENABLED"))
            if not enabled:
                return [], _provider_attempt(provider, "skipped", reason="disabled")
            return self._try_json_search_provider(
                provider,
                lambda: self._search_marginalia(
                    query=query,
                    limit=limit,
                    allowed_domains=allowed_domains,
                    api_key=api_key or "public",
                ),
            )
        if provider == "duckduckgo":
            try:
                return self.search(
                    query,
                    limit=limit,
                    allowed_domains=allowed_domains,
                ), _provider_attempt(provider, "success")
            except InternetToolError as exc:
                return [], _provider_attempt(
                    provider,
                    "error",
                    reason="request_failed",
                    error=_provider_error_message(exc),
                )
        return [], _provider_attempt(provider, "skipped", reason="unknown_provider")

    def _try_json_search_provider(
        self, provider: str, loader: Any
    ) -> tuple[list[SearchResult], dict[str, Any]]:
        try:
            return loader(), _provider_attempt(provider, "success")
        except Exception as exc:
            return [], _provider_attempt(
                provider,
                "error",
                reason="request_failed",
                error=_provider_error_message(exc),
            )

    def _search_brave(
        self,
        *,
        query: str,
        limit: int,
        allowed_domains: list[str],
        api_key: str,
    ) -> list[SearchResult]:
        params = {
            "q": query,
            "count": min(max(limit, 1), 20),
            "safesearch": _first_env("AUTOSTOP_SEARCH_SAFESEARCH") or "moderate",
            "result_filter": "web",
            "extra_snippets": "true",
        }
        for env_name, param_name in (
            ("AUTOSTOP_SEARCH_COUNTRY", "country"),
            ("AUTOSTOP_SEARCH_LANG", "search_lang"),
            ("AUTOSTOP_SEARCH_UI_LANG", "ui_lang"),
            ("AUTOSTOP_SEARCH_FRESHNESS", "freshness"),
        ):
            value = _first_env(env_name)
            if value:
                params[param_name] = value
        with httpx.Client(
            timeout=self._timeout_seconds,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            trust_env=False,
        ) as client:
            payload = self._fetch_limited_json(
                client,
                "GET",
                _BRAVE_SEARCH_URL,
                params=params,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
                max_bytes=_MAX_SEARCH_RESPONSE_BYTES,
            )
        raw_results = _as_list(_as_dict(payload).get("web"), key="results")
        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            snippets = [item.get("description")]
            snippets.extend(_as_text_list(item.get("extra_snippets")))
            result = self._search_result_from_json(
                provider="brave",
                title=item.get("title"),
                url=item.get("url"),
                snippet=" ".join(str(part or "") for part in snippets),
                allowed_domains=allowed_domains,
            )
            if result is not None:
                results.append(result)
        return results[:limit]

    def _search_tavily(
        self,
        *,
        query: str,
        limit: int,
        allowed_domains: list[str],
        api_key: str,
    ) -> list[SearchResult]:
        body: dict[str, Any] = {
            "query": query,
            "search_depth": _first_env("TAVILY_SEARCH_DEPTH") or "basic",
            "max_results": limit,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        if allowed_domains:
            body["include_domains"] = allowed_domains
        with httpx.Client(
            timeout=self._timeout_seconds,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            trust_env=False,
        ) as client:
            payload = self._fetch_limited_json(
                client,
                "POST",
                _TAVILY_SEARCH_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                max_bytes=_MAX_SEARCH_RESPONSE_BYTES,
            )
        results: list[SearchResult] = []
        for item in _as_list(payload, key="results"):
            if not isinstance(item, dict):
                continue
            result = self._search_result_from_json(
                provider="tavily",
                title=item.get("title"),
                url=item.get("url"),
                snippet=item.get("content") or item.get("snippet"),
                allowed_domains=allowed_domains,
            )
            if result is not None:
                results.append(result)
        return results[:limit]

    def _search_google_cse(
        self,
        *,
        query: str,
        limit: int,
        allowed_domains: list[str],
        api_key: str,
        cx: str,
    ) -> list[SearchResult]:
        params = {
            "key": api_key,
            "cx": cx,
            "q": query,
            "num": min(max(limit, 1), 10),
            "safe": "active",
        }
        with httpx.Client(
            timeout=self._timeout_seconds,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            trust_env=False,
        ) as client:
            payload = self._fetch_limited_json(
                client,
                "GET",
                _GOOGLE_CSE_SEARCH_URL,
                params=params,
                max_bytes=_MAX_SEARCH_RESPONSE_BYTES,
            )
        results: list[SearchResult] = []
        for item in _as_list(payload, key="items"):
            if not isinstance(item, dict):
                continue
            result = self._search_result_from_json(
                provider="google_cse",
                title=item.get("title"),
                url=item.get("link"),
                snippet=item.get("snippet"),
                allowed_domains=allowed_domains,
            )
            if result is not None:
                results.append(result)
        return results[:limit]

    def _search_searxng(
        self,
        *,
        query: str,
        limit: int,
        allowed_domains: list[str],
        base_url: str,
    ) -> list[SearchResult]:
        params = {
            "q": query,
            "format": "json",
            "categories": _first_env("AUTOSTOP_SEARXNG_CATEGORIES") or "general",
            "safesearch": _first_env("AUTOSTOP_SEARCH_SAFESEARCH") or "0",
        }
        language = _first_env("AUTOSTOP_SEARCH_LANG")
        if language:
            params["language"] = language
        endpoint = _searxng_search_endpoint(base_url)
        with httpx.Client(
            timeout=self._timeout_seconds,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            trust_env=False,
        ) as client:
            payload = self._fetch_limited_json(
                client,
                "GET",
                endpoint,
                params=params,
                max_bytes=_MAX_SEARCH_RESPONSE_BYTES,
                public=False,
            )
        results: list[SearchResult] = []
        for item in _as_list(payload, key="results"):
            if not isinstance(item, dict):
                continue
            result = self._search_result_from_json(
                provider="searxng",
                title=item.get("title"),
                url=item.get("url"),
                snippet=item.get("content") or item.get("snippet") or item.get("description"),
                allowed_domains=allowed_domains,
            )
            if result is not None:
                results.append(result)
            if len(results) >= limit:
                break
        return results[:limit]

    def _search_marginalia(
        self,
        *,
        query: str,
        limit: int,
        allowed_domains: list[str],
        api_key: str,
    ) -> list[SearchResult]:
        params = {
            "query": query,
            "count": str(min(max(limit, 1), 10)),
            "nsfw": _first_env("AUTOSTOP_MARGINALIA_NSFW") or "1",
        }
        with httpx.Client(
            timeout=self._timeout_seconds,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            trust_env=False,
        ) as client:
            payload = self._fetch_limited_json(
                client,
                "GET",
                _MARGINALIA_SEARCH_URL,
                params=params,
                headers={"API-Key": api_key},
                max_bytes=_MAX_SEARCH_RESPONSE_BYTES,
            )
        results: list[SearchResult] = []
        for item in _as_list(payload, key="results"):
            if not isinstance(item, dict):
                continue
            result = self._search_result_from_json(
                provider="marginalia",
                title=item.get("title"),
                url=item.get("url"),
                snippet=item.get("description") or item.get("snippet"),
                allowed_domains=allowed_domains,
            )
            if result is not None:
                results.append(result)
            if len(results) >= limit:
                break
        return results[:limit]

    def _search_result_from_json(
        self,
        *,
        provider: str,
        title: Any,
        url: Any,
        snippet: Any,
        allowed_domains: list[str],
    ) -> SearchResult | None:
        try:
            normalized_url = self._validated_public_http_url(
                str(url or "").strip(), resolve_dns=False
            )
        except InternetToolError:
            return None
        domain = self._url_hostname(normalized_url)
        if allowed_domains and not _domain_allowed(domain, allowed_domains):
            return None
        return SearchResult(
            title=self._clean_html_text(str(title or ""))[:240],
            url=normalized_url,
            snippet=self._clean_html_text(str(snippet or ""))[:700],
            domain=domain,
            provider=provider,
        )

    def _resolve_duckduckgo_url(self, href: str) -> str:
        raw_href = html.unescape(str(href or ""))
        if raw_href.startswith("//duckduckgo.com/l/?"):
            parsed = urlparse("https:" + raw_href)
            encoded = parse_qs(parsed.query).get("uddg", [""])[0]
            return unquote(encoded) if encoded else ""
        if raw_href.startswith("/l/?"):
            parsed = urlparse("https://duckduckgo.com" + raw_href)
            encoded = parse_qs(parsed.query).get("uddg", [""])[0]
            return unquote(encoded) if encoded else ""
        return raw_href

    def _url_hostname(self, url: str) -> str:
        try:
            return str(urlparse(url).hostname or "").strip().casefold().rstrip(".")
        except ValueError:
            return ""

    def _launch_chromium(self, playwright: Any) -> Any:
        errors: list[str] = []
        launch_kwargs = {
            "headless": True,
            "args": ["--disable-dev-shm-usage", "--no-sandbox"],
        }
        try:
            return playwright.chromium.launch(**launch_kwargs)
        except Exception as exc:
            errors.append(_safe_error_message(exc))
        for executable_path in _browser_executable_candidates():
            try:
                return playwright.chromium.launch(
                    executable_path=executable_path,
                    **launch_kwargs,
                )
            except Exception as exc:
                errors.append(f"{executable_path}: {_safe_error_message(exc)}")
        tail = " | ".join(errors[-3:]) if errors else "unknown launch error"
        raise InternetToolError(f"Chromium browser could not start: {tail}")

    def _browser_title(self, page: Any) -> str:
        try:
            return self._clean_html_text(str(page.title() or ""))[:200]
        except Exception:
            return ""

    def _browser_text(self, page: Any) -> str:
        try:
            body_text = str(page.locator("body").inner_text(timeout=2000))
            return _MULTISPACE_PATTERN.sub(" ", body_text).strip()
        except Exception:
            return ""

    def _browser_links(self, page: Any, *, base_url: str) -> list[dict[str, str]]:
        try:
            raw_links = page.eval_on_selector_all(
                "a[href]",
                """
                nodes => nodes.map(node => ({
                    text: (node.innerText || node.textContent || '').trim(),
                    url: node.href || node.getAttribute('href') || ''
                }))
                """,
            )
        except Exception:
            raw_links = []
        links: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_links if isinstance(raw_links, list) else []:
            if not isinstance(item, dict):
                continue
            raw_url = str(item.get("url") or "").strip()
            if not raw_url:
                continue
            try:
                resolved_url = self._validated_public_http_url(
                    urljoin(base_url, raw_url), resolve_dns=False
                )
            except InternetToolError:
                continue
            if resolved_url in seen:
                continue
            seen.add(resolved_url)
            links.append(
                {
                    "text": _MULTISPACE_PATTERN.sub(" ", str(item.get("text") or "")).strip()[:160],
                    "url": resolved_url,
                    "domain": self._url_hostname(resolved_url),
                }
            )
            if len(links) >= _MAX_BROWSER_LINKS:
                break
        return links

    def _browser_error_payload(
        self,
        url: str,
        *,
        error: str,
        message: str,
        access_flags: list[str],
    ) -> dict[str, Any]:
        flags = _unique_flags(access_flags)
        return {
            "ok": False,
            "url": url,
            "final_url": "",
            "domain": self._url_hostname(url),
            "title": "",
            "status_code": 0,
            "excerpt": "",
            "links": [],
            "access_flags": flags,
            "requires_human": any(flag in _HUMAN_REQUIRED_FLAGS for flag in flags),
            "engine": "playwright_chromium",
            "mode": "browser",
            "error": error,
            "message": message,
        }

    def _clean_html_text(self, value: str) -> str:
        text = _SCRIPT_STYLE_PATTERN.sub(" ", str(value or ""))
        text = _TAG_PATTERN.sub(" ", text)
        text = html.unescape(text)
        text = _MULTISPACE_PATTERN.sub(" ", text)
        return text.strip()


def _load_sync_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    return sync_playwright


def _browser_egress_isolation_verified() -> bool:
    """Keep browser navigation disabled until deployment adds a real ACL boundary.

    This intentionally has no environment override: a process flag cannot prove
    that Chromium is unable to connect to loopback, private, link-local, metadata,
    or Docker-network targets after DNS rebinding.  A future isolated renderer must
    replace this with its independently verifiable network contract.
    """

    return False


def _browser_executable_candidates() -> list[str]:
    candidates: list[str] = []
    explicit_path = str(os.environ.get("AUTOSTOP_BROWSER_EXECUTABLE_PATH") or "").strip()
    if explicit_path:
        candidates.append(explicit_path)
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        path = shutil.which(name)
        if path and path not in candidates:
            candidates.append(path)
    return candidates


def _detect_access_flags(value: str, *, status_code: int = 0) -> list[str]:
    flags = [name for name, pattern in _ACCESS_FLAG_PATTERNS if pattern.search(str(value or ""))]
    if status_code in {401, 403}:
        flags.append("access_denied")
    if status_code == 429:
        flags.append("rate_limited")
    return _unique_flags(flags)


def _unique_flags(flags: list[str]) -> list[str]:
    result: list[str] = []
    for flag in flags:
        normalized = str(flag or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _safe_error_message(exc: BaseException) -> str:
    return _MULTISPACE_PATTERN.sub(" ", str(exc or "")).strip()[:400]


def _provider_error_message(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = getattr(getattr(exc, "response", None), "status_code", "")
        return f"HTTP {status_code}".strip()
    if isinstance(exc, httpx.HTTPError):
        return exc.__class__.__name__
    return _redact_configured_secrets(_safe_error_message(exc))


def _redact_configured_secrets(message: str) -> str:
    redacted = str(message or "")
    for env_name in (
        "BRAVE_SEARCH_API_KEY",
        "BRAVE_API_KEY",
        "TAVILY_API_KEY",
        "GOOGLE_CUSTOM_SEARCH_API_KEY",
        "GOOGLE_CSE_API_KEY",
        "AUTOSTOP_MARGINALIA_API_KEY",
        "MARGINALIA_API_KEY",
        "AUTOSTOP_CRAWL4AI_API_TOKEN",
        "CRAWL4AI_API_TOKEN",
        "AUTOSTOP_CRAWL4AI_SECRET_KEY",
        "CRAWL4AI_SECRET_KEY",
    ):
        secret = _first_env(env_name)
        if secret and len(secret) >= 4:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _first_env(*names: str) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _provider_attempt(
    provider: str,
    status: str,
    *,
    reason: str = "",
    error: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"provider": provider, "status": status}
    if reason:
        payload["reason"] = reason
    if error:
        payload["error"] = error
    return payload


def _normalize_provider_order(value: Any) -> list[str]:
    disabled = _disabled_search_providers()
    if value is None:
        configured = _first_env("AUTOSTOP_SEARCH_PROVIDER_ORDER")
        raw_items: list[Any] = re.split(r"[\s,]+", configured) if configured else []
    elif isinstance(value, str):
        raw_items = re.split(r"[\s,]+", value)
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        provider = _SEARCH_PROVIDER_ALIASES.get(str(raw or "").strip().casefold(), "")
        if provider and provider not in disabled and provider not in seen:
            ordered.append(provider)
            seen.add(provider)
    for provider in _SEARCH_PROVIDER_ORDER:
        if provider not in disabled and provider not in seen:
            ordered.append(provider)
            seen.add(provider)
    return ordered


def _disabled_search_providers() -> set[str]:
    raw = _first_env("AUTOSTOP_SEARCH_DISABLED_PROVIDERS")
    disabled: set[str] = set()
    for item in re.split(r"[\s,]+", raw):
        provider = _SEARCH_PROVIDER_ALIASES.get(str(item or "").strip().casefold(), "")
        if provider:
            disabled.add(provider)
    return disabled


def _searxng_search_endpoint(base_url: str) -> str:
    url = str(base_url or "").strip().rstrip("/")
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.path.rstrip("/").endswith("/search"):
        return url
    return f"{url}/search"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "on", "enabled"}


def _domain_allowed(domain: str, allowed_domains: list[str]) -> bool:
    normalized = str(domain or "").strip().casefold().rstrip(".")
    return bool(
        normalized
        and any(normalized == item or normalized.endswith(f".{item}") for item in allowed_domains)
    )


def _canonical_result_url(url: str) -> str:
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return ""
    scheme = str(parsed.scheme or "").casefold()
    host = str(parsed.hostname or "").casefold().rstrip(".")
    if not scheme or not host:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(
        scheme=scheme,
        netloc=f"{host}{port}",
        path=path,
        fragment="",
    ).geturl()


def _http_host_header(host: str, port: int | None, scheme: str) -> str:
    bracketed_host = f"[{host}]" if ":" in host else host
    if port is None or (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        return bracketed_host
    return f"{bracketed_host}:{port}"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any, *, key: str) -> list[Any]:
    if isinstance(value, dict):
        items = value.get(key)
        return items if isinstance(items, list) else []
    return []


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item or "") for item in value if str(item or "").strip()]
    if str(value or "").strip():
        return [str(value)]
    return []


def _normalize_int(value: Any, *, default: int, minimum: int = 1, maximum: int) -> int:
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(numeric) or not numeric.is_integer():
        return default
    if numeric < minimum:
        return default
    if numeric > maximum:
        return maximum
    return int(numeric)


def _normalize_seconds(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed):
        return default
    return min(max(parsed, minimum), maximum)


def _normalize_domain_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    domains: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        domain = _normalize_allowed_domain(raw)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        domains.append(domain)
    return domains


def _normalize_allowed_domain(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    parse_target = text if "://" in text else f"//{text}"
    try:
        parsed = urlparse(parse_target)
    except ValueError:
        return ""
    host = (parsed.hostname or "").strip().casefold().strip(".")
    return host.lstrip(".")
