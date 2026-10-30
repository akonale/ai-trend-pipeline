"""Safe, bounded HTTP fetching and text normalization for story research.

Every hop is validated before it is requested: scheme, userinfo, port, URL length, host-name
policy, approved-host lists, and the resolved destination addresses. The fetcher never follows
redirects automatically, never trusts environment proxies, and never logs or returns raw bodies
or full URLs in failures.

Residual risk: `httpx` resolves the host again at connect time, so a DNS-rebinding time-of-check
to time-of-use gap remains. The checks here prove the pre-connect policy, not absolute prevention.
"""

import asyncio
import ipaddress
import logging
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser

import httpx
from pydantic import HttpUrl

from ai_trend_pipeline.research.models import (
    ALLOWED_CONTENT_TYPES,
    ALLOWED_PORTS,
    MAX_CONTEXT_TEXT_CHARS,
    MAX_TITLE_CHARS,
    REDIRECT_STATUSES,
    FetchContextOutput,
    ResearchFailure,
    ResearchFailureCode,
    ResearchNoteCode,
    StoryContext,
    context_id_for,
    normalize_host_name,
)

HostResolver = Callable[[str], Awaitable[Sequence[str]]]

_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".lan")
_URL_LIMIT = 1024
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "head"})
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
_FAILURE_MESSAGES = {
    ResearchFailureCode.NO_SOURCE_URL: "The story has no source URL.",
    ResearchFailureCode.UNSAFE_URL: "The URL is not allowed.",
    ResearchFailureCode.BLOCKED_NETWORK: "The destination network is not allowed.",
    ResearchFailureCode.TOO_MANY_REDIRECTS: "The redirect limit was exceeded.",
    ResearchFailureCode.MISSING_ARTICLE: "The article is missing.",
    ResearchFailureCode.FETCH_FAILED: "The article could not be fetched.",
    ResearchFailureCode.UNSUPPORTED_CONTENT_TYPE: "The content type is not supported.",
    ResearchFailureCode.MALFORMED_CONTENT: "The page had no readable text.",
    ResearchFailureCode.INSUFFICIENT_EVIDENCE: "The article text is too short.",
}


class _NoUrlAccessLogFilter(logging.Filter):
    """Keep full URLs and query strings out of httpx's request/response access log lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not message.startswith(("HTTP Request:", "HTTP Response:"))


logging.getLogger("httpx").addFilter(_NoUrlAccessLogFilter())


@dataclass(frozen=True)
class NormalizedContent:
    title: str | None
    text: str
    truncated: bool


class RetryableFetchError(httpx.HTTPError):
    """A transient upstream status; the message never contains a URL or query string."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"retryable upstream status {status_code}")
        self.status_code = status_code


async def default_host_resolver(host: str) -> Sequence[str]:
    """Resolve a host to its IP strings for the pre-connect policy check."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _collapse(value: str) -> str:
    return _WHITESPACE.sub(" ", _CONTROL_CHARS.sub("", value)).strip()


def _cap(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def normalize_plain_text(
    value: str, *, max_chars: int = MAX_CONTEXT_TEXT_CHARS
) -> NormalizedContent:
    text, cut = _cap(_WHITESPACE.sub(" ", value).strip(), max_chars)
    return NormalizedContent(title=None, text=text, truncated=cut)


class _TextExtractor(HTMLParser):
    def __init__(self, max_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self._max_chars = max_chars
        # Collect a little more than the cap so whitespace collapsing can still fill it.
        self._raw_limit = max_chars * 2
        self._parts: list[str] = []
        self._length = 0
        self._capped = False
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._title_length = 0
        self._title_capped = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name == "title":
            self._in_title = True
        if name in _SKIP_TAGS:
            self._skip_depth += 1
        if name in _BLOCK_TAGS:
            self._add(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in _BLOCK_TAGS:
            self._add(" ")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name == "title":
            self._in_title = False
        if name in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if name in _BLOCK_TAGS:
            self._add(" ")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._add_title(data)
            return
        if self._skip_depth == 0:
            self._add(data)

    def _add(self, text: str) -> None:
        if self._capped:
            return
        remaining = self._raw_limit - self._length
        if remaining <= 0:
            self._capped = True
            return
        if len(text) > remaining:
            self._parts.append(text[:remaining])
            self._length = self._raw_limit
            self._capped = True
            return
        self._parts.append(text)
        self._length += len(text)

    def _add_title(self, text: str) -> None:
        if self._title_capped:
            return
        remaining = MAX_TITLE_CHARS - self._title_length
        if remaining <= 0:
            self._title_capped = True
            return
        if len(text) > remaining:
            self._title_parts.append(text[:remaining])
            self._title_length = MAX_TITLE_CHARS
            self._title_capped = True
            return
        self._title_parts.append(text)
        self._title_length += len(text)

    def result(self) -> NormalizedContent:
        raw = "".join(self._parts)
        text, text_cut = _cap(_collapse(raw), self._max_chars)
        title = _collapse("".join(self._title_parts)) or None
        return NormalizedContent(
            title=title,
            text=text,
            truncated=self._capped or text_cut,
        )


def normalize_html(value: str, *, max_chars: int = MAX_CONTEXT_TEXT_CHARS) -> NormalizedContent:
    parser = _TextExtractor(max_chars)
    try:
        parser.feed(value)
        parser.close()
    except Exception:  # pragma: no cover - stdlib parser is tolerant; fail closed
        return NormalizedContent(title=None, text="", truncated=False)
    return parser.result()


def _media_type(raw: str | None) -> str:
    if not raw:
        return ""
    return raw.split(";", 1)[0].strip().lower()


def _charset(raw: str | None) -> str:
    if not raw:
        return "utf-8"
    for parameter in raw.split(";")[1:]:
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "charset":
            return value.strip().strip('"').lower() or "utf-8"
    return "utf-8"


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    candidate = value.strip().strip("[]")
    if "%" in candidate:
        candidate = candidate.split("%", 1)[0]
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def _is_global(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_unspecified
    )


def _host_key(url: httpx.URL) -> str:
    return (url.host or "").lower().rstrip(".")


def _is_unsafe_host(host: str) -> bool:
    candidate = host.lower().rstrip(".")
    if not candidate or len(candidate) > 253:
        return True
    if candidate == "localhost" or candidate.endswith(_LOCAL_SUFFIXES):
        return True
    if _parse_ip(candidate) is not None:
        return False
    return "." not in candidate


def _safe_host(url: httpx.URL) -> str | None:
    try:
        return normalize_host_name(url.host or "")
    except ValueError:
        return None


def _failure(
    requested_url: HttpUrl,
    code: ResearchFailureCode,
    url: httpx.URL | None = None,
) -> FetchContextOutput:
    host = _safe_host(url) if url is not None else None
    return FetchContextOutput(
        requested_url=requested_url,
        failure=ResearchFailure(code=code, message=_FAILURE_MESSAGES[code], host=host),
        notes=[],
    )


class SafeFetcher:
    """Fetch one URL under explicit limits, validating every hop before the request."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        timeout_s: float,
        max_response_bytes: int,
        max_redirects: int,
        user_agent: str,
        resolver: HostResolver | None = None,
    ) -> None:
        self._client = client
        self._timeout_s = timeout_s
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._user_agent = user_agent
        self._resolver: HostResolver = resolver or default_host_resolver

    async def fetch(
        self,
        url: str | httpx.URL,
        *,
        approved_hosts: frozenset[str] | None = None,
    ) -> FetchContextOutput:
        requested = HttpUrl(str(url))
        requested_text = str(requested)
        current = httpx.URL(requested_text)
        approved = (
            frozenset(host.strip().lower().rstrip(".") for host in approved_hosts)
            if approved_hosts is not None
            else None
        )
        redirects_followed = 0

        while True:
            if not self._url_is_safe(current):
                return _failure(requested, ResearchFailureCode.UNSAFE_URL, current)
            if approved is not None and _host_key(current) not in approved:
                return _failure(requested, ResearchFailureCode.UNSAFE_URL, current)

            network = await self._network_blocked(current)
            if network is not None:
                return _failure(requested, network, current)

            async with self._client.stream(
                "GET",
                current,
                headers={
                    "accept-encoding": "identity",
                    "user-agent": self._user_agent,
                },
                timeout=self._timeout_s,
            ) as response:
                status = response.status_code
                if status in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        return _failure(requested, ResearchFailureCode.FETCH_FAILED, current)
                    if redirects_followed >= self._max_redirects:
                        return _failure(
                            requested,
                            ResearchFailureCode.TOO_MANY_REDIRECTS,
                            current,
                        )
                    redirects_followed += 1
                    try:
                        current = current.join(location)
                    except (ValueError, httpx.InvalidURL):
                        return _failure(requested, ResearchFailureCode.UNSAFE_URL, current)
                    continue

                if status in (404, 410):
                    return _failure(requested, ResearchFailureCode.MISSING_ARTICLE, current)
                if status in (408, 425, 429) or 500 <= status <= 599:
                    raise RetryableFetchError(status)
                if status >= 300:
                    return _failure(requested, ResearchFailureCode.FETCH_FAILED, current)

                content_type = response.headers.get("content-type")
                if _media_type(content_type) not in ALLOWED_CONTENT_TYPES:
                    return _failure(
                        requested,
                        ResearchFailureCode.UNSUPPORTED_CONTENT_TYPE,
                        current,
                    )

                body, body_cut = await self._read_bounded(response)
                charset = _charset(content_type)
                try:
                    decoded = body.decode(charset, errors="replace")
                except LookupError:
                    decoded = body.decode("utf-8", errors="replace")

                if _media_type(content_type) == "text/plain":
                    normalized = normalize_plain_text(decoded)
                else:
                    normalized = normalize_html(decoded)
                if not normalized.text:
                    return _failure(requested, ResearchFailureCode.MALFORMED_CONTENT, current)

                truncated = body_cut or normalized.truncated
                notes = [ResearchNoteCode.RESPONSE_TRUNCATED] if truncated else []
                final_url = HttpUrl(str(current))
                return FetchContextOutput(
                    requested_url=requested,
                    context=StoryContext(
                        context_id=context_id_for(str(final_url)),
                        requested_url=requested,
                        url=final_url,
                        title=normalized.title,
                        publisher=_host_key(current),
                        content_type=_media_type(content_type),
                        text=normalized.text,
                        truncated=truncated,
                        retrieved_at=datetime_now_utc(),
                    ),
                    notes=notes,
                )

    def _url_is_safe(self, url: httpx.URL) -> bool:
        if len(str(url)) > _URL_LIMIT:
            return False
        if url.scheme not in {"http", "https"}:
            return False
        if url.userinfo:
            return False
        host = url.host
        if not host:
            return False
        if url.port is not None and url.port not in ALLOWED_PORTS:
            return False
        return not _is_unsafe_host(host)

    async def _network_blocked(self, url: httpx.URL) -> ResearchFailureCode | None:
        host = url.host or ""
        literal = _parse_ip(host)
        if literal is not None:
            return None if _is_global(literal) else ResearchFailureCode.BLOCKED_NETWORK
        try:
            addresses = await self._resolver(host)
        except OSError as exc:
            request = httpx.Request("GET", url)
            raise httpx.ConnectError("DNS resolution failed", request=request) from exc
        if not addresses:
            request = httpx.Request("GET", url)
            raise httpx.ConnectError("DNS resolution returned no addresses", request=request)
        for address in addresses:
            parsed = _parse_ip(address)
            if parsed is None or not _is_global(parsed):
                return ResearchFailureCode.BLOCKED_NETWORK
        return None

    async def _read_bounded(self, response: httpx.Response) -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        total = 0
        truncated = False
        async for chunk in response.aiter_bytes():
            remaining = self._max_response_bytes - total
            if len(chunk) > remaining:
                if remaining > 0:
                    chunks.append(chunk[:remaining])
                total = self._max_response_bytes
                truncated = True
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks), truncated


def datetime_now_utc() -> datetime:
    return datetime.now(UTC)
