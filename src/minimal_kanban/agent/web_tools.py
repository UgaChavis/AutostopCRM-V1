from __future__ import annotations

import html
import ipaddress
import math
import re
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
_DEFAULT_SEARCH_LIMIT = 5
_MAX_SEARCH_LIMIT = 10
_DEFAULT_PAGE_EXCERPT_CHARS = 2500
_MAX_PAGE_EXCERPT_CHARS = 10_000
_DEFAULT_TIMEOUT_SECONDS = 12.0
_MAX_SEARCH_RESPONSE_BYTES = 1_500_000
_MAX_PAGE_RESPONSE_BYTES = 2_000_000
_MAX_REDIRECTS = 5
_BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".test", ".invalid")


class InternetToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    domain: str

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "domain": self.domain,
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
        query_text = str(query or "").strip()
        if not query_text:
            raise InternetToolError("query is required")
        normalized_limit = _normalize_int(
            limit, default=_DEFAULT_SEARCH_LIMIT, maximum=_MAX_SEARCH_LIMIT
        )
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query_text)}"
        try:
            with httpx.Client(
                timeout=self._timeout_seconds, headers={"User-Agent": "Mozilla/5.0 AutoStopCRM/1.0"}
            ) as client:
                html_text = self._fetch_limited_text(client, url, _MAX_SEARCH_RESPONSE_BYTES)
        except httpx.HTTPError as exc:
            raise InternetToolError(f"Web search failed: {exc}") from exc
        return self._parse_results(
            html_text, limit=normalized_limit, allowed_domains=allowed_domains
        )

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
                timeout=self._timeout_seconds, headers={"User-Agent": "Mozilla/5.0 AutoStopCRM/1.0"}
            ) as client:
                response_url, html_text = self._fetch_limited_text_with_url(
                    client, normalized_url, _MAX_PAGE_RESPONSE_BYTES
                )
        except httpx.HTTPError as exc:
            raise InternetToolError(f"Page fetch failed: {exc}") from exc
        text = self._clean_html_text(html_text)
        return {
            "url": response_url,
            "domain": self._url_hostname(response_url),
            "excerpt": text[:normalized_max_chars],
        }

    def _fetch_limited_text(self, client: httpx.Client, url: str, max_bytes: int) -> str:
        return self._fetch_limited_text_with_url(client, url, max_bytes)[1]

    def _fetch_limited_text_with_url(
        self, client: httpx.Client, url: str, max_bytes: int
    ) -> tuple[str, str]:
        current_url = self._validated_public_http_url(url)
        for _ in range(_MAX_REDIRECTS + 1):
            with client.stream("GET", current_url, follow_redirects=False) as response:
                status_code = int(getattr(response, "status_code", 200) or 200)
                if 300 <= status_code < 400:
                    location = str(getattr(response, "headers", {}).get("location", "") or "")
                    if not location:
                        raise InternetToolError("Web redirect is missing a Location header.")
                    current_url = self._validated_public_http_url(urljoin(current_url, location))
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes(chunk_size=max_bytes + 1):
                    total += len(chunk)
                    if total > max_bytes:
                        raise InternetToolError("Web response is too large.")
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                text = b"".join(chunks).decode(encoding, errors="replace")
                return str(response.url), text
        raise InternetToolError("Web redirect chain is too long.")

    def _validated_public_http_url(self, url: str) -> str:
        try:
            parsed = urlparse(url)
        except ValueError as exc:
            raise InternetToolError("Only public HTTP(S) URLs are supported.") from exc
        scheme = str(parsed.scheme or "").casefold()
        host = str(parsed.hostname or "").strip().casefold().rstrip(".")
        if scheme not in {"http", "https"} or not parsed.netloc or not host:
            raise InternetToolError("Only public HTTP(S) URLs are supported.")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES) or "." not in host:
                raise InternetToolError("Local or private URLs are not supported.")
            return url
        if not address.is_global:
            raise InternetToolError("Local or private URLs are not supported.")
        return url

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
                resolved_url = self._validated_public_http_url(resolved_url)
            except InternetToolError:
                continue
            domain = self._url_hostname(resolved_url)
            if allowed and not any(
                domain == item or domain.endswith(f".{item}") for item in allowed
            ):
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

    def _clean_html_text(self, value: str) -> str:
        text = _SCRIPT_STYLE_PATTERN.sub(" ", str(value or ""))
        text = _TAG_PATTERN.sub(" ", text)
        text = html.unescape(text)
        text = _MULTISPACE_PATTERN.sub(" ", text)
        return text.strip()


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
