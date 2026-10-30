"""Safe fetcher tests: URL policy, DNS checks, redirects, bounds and normalization."""

import logging
import socket
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import httpx
import pytest

from ai_trend_pipeline.config import load_settings
from ai_trend_pipeline.research import (
    MAX_CONTEXT_TEXT_CHARS,
    FetchContextOutput,
    ResearchFailureCode,
    ResearchNoteCode,
)
from ai_trend_pipeline.research.activities import make_research_client
from ai_trend_pipeline.research.fetch import SafeFetcher, normalize_html, normalize_plain_text

FIXTURES = Path(__file__).parent / "fixtures" / "research"
PUBLIC_IP = "93.184.216.34"
Resolver = Callable[[str], Awaitable[Sequence[str]]]


def public_resolver(calls: list[str] | None = None) -> Resolver:
    async def resolve(host: str) -> Sequence[str]:
        if calls is not None:
            calls.append(host)
        return [PUBLIC_IP]

    return resolve


def failing_resolver(exc: BaseException) -> Resolver:
    async def resolve(host: str) -> Sequence[str]:
        raise exc

    return resolve


def make_fetcher(
    handler: Callable[[httpx.Request], httpx.Response],
    resolver: Resolver | None = None,
    *,
    max_response_bytes: int = 1_048_576,
    max_redirects: int = 3,
) -> SafeFetcher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SafeFetcher(
        client,
        timeout_s=5.0,
        max_response_bytes=max_response_bytes,
        max_redirects=max_redirects,
        user_agent="test-agent",
        resolver=resolver or public_resolver(),
    )


async def run_fetch(
    fetcher: SafeFetcher,
    url: str,
    *,
    approved_hosts: frozenset[str] | None = None,
) -> FetchContextOutput:
    return await fetcher.fetch(url, approved_hosts=approved_hosts)


def html_response(body: str, *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})


def explode(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected request: {request.url}")


@pytest.mark.asyncio
async def test_fetch_normalizes_html_extracts_title_and_strips_scripts() -> None:
    body = (FIXTURES / "article_supported.html").read_text()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return html_response(body)

    result = await run_fetch(make_fetcher(handler), "https://news.example/story")

    assert len(calls) == 1
    assert result.failure is None
    context = result.context
    assert context is not None
    assert context.title == "Life expectancy keeps rising across most regions"
    assert str(context.requested_url) == "https://news.example/story"
    assert str(context.url) == "https://news.example/story"
    assert context.publisher == "news.example"
    assert context.content_type == "text/html"
    assert "<p>" not in context.text
    assert "do-not-capture-this-script" not in context.text
    assert "another script that must never appear" not in context.text
    assert "color: #222" not in context.text
    assert "Home · Health · Data" in context.text
    assert "Copyright © 2026 Example News" in context.text
    assert "  " not in context.text
    assert len(context.text) >= 300
    assert context.truncated is False
    assert result.notes == []
    assert context.context_id.startswith("ctx-v1-")


def test_normalize_plain_text_only_collapses_whitespace() -> None:
    normalized = normalize_plain_text("<b>not markup</b>\r\n\r\n  kept  as  text  ")
    assert normalized.text == "<b>not markup</b> kept as text"
    assert normalized.title is None


def test_normalize_html_respects_text_cap() -> None:
    normalized = normalize_html("<html><body><p>" + ("word " * 5000) + "</p></body></html>")
    assert len(normalized.text) == MAX_CONTEXT_TEXT_CHARS
    assert normalized.truncated is True


@pytest.mark.asyncio
async def test_fetch_rejects_userinfo_disallowed_ports_and_non_http_redirects() -> None:
    fetcher = make_fetcher(explode)
    for url in (
        "https://user:pass@example.com/secret",
        "https://example.com:8443/secret",
        "http://example.com:8080/secret",
    ):
        result = await run_fetch(fetcher, url)
        assert result.context is None
        assert result.failure is not None
        assert result.failure.code is ResearchFailureCode.UNSAFE_URL

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "ftp://example.com/file"})

    redirect = await run_fetch(make_fetcher(handler), "https://example.com/start")
    assert redirect.failure is not None
    assert redirect.failure.code is ResearchFailureCode.UNSAFE_URL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/story",
        "http://news/story",
        "http://foo.local/story",
        "http://foo.internal/story",
        "http://foo.home.arpa/story",
        "http://foo.lan/story",
    ],
)
async def test_fetch_rejects_localhost_single_label_and_internal_suffixes(url: str) -> None:
    result = await run_fetch(make_fetcher(explode), url)
    assert result.context is None
    assert result.failure is not None
    assert result.failure.code is ResearchFailureCode.UNSAFE_URL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "127.0.0.1",
        "169.254.1.1",
        "192.168.1.1",
        "172.16.0.1",
        "100.64.0.1",
        "224.0.0.1",
        "0.0.0.0",
        "240.0.0.1",
        "::1",
        "::ffff:127.0.0.1",
        "fe80::1",
        "fc00::1",
    ],
)
async def test_fetch_blocks_private_loopback_link_local_reserved_and_ipv6_destinations(
    address: str,
) -> None:
    async def resolver(host: str) -> Sequence[str]:
        return [address]

    result = await run_fetch(make_fetcher(explode, resolver), "https://news.example/story")
    assert result.context is None
    assert result.failure is not None
    assert result.failure.code is ResearchFailureCode.BLOCKED_NETWORK

    literal_host = f"[{address}]" if ":" in address else address
    literal = await run_fetch(
        make_fetcher(explode, failing_resolver(RuntimeError("no dns"))),
        f"http://{literal_host}/x",
    )
    assert literal.failure is not None
    assert literal.failure.code is ResearchFailureCode.BLOCKED_NETWORK


@pytest.mark.asyncio
async def test_fetch_allows_public_hosts_and_public_literal_ips() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return html_response("<html><body><p>Hello from the public web.</p></body></html>")

    calls: list[str] = []
    host_result = await run_fetch(
        make_fetcher(handler, public_resolver(calls)), "https://news.example/story"
    )
    assert host_result.context is not None
    assert calls == ["news.example"]

    async def no_dns(host: str) -> Sequence[str]:
        raise AssertionError("literal IPs must not be resolved")

    literal_result = await run_fetch(make_fetcher(handler, no_dns), f"https://{PUBLIC_IP}/story")
    assert literal_result.context is not None
    assert str(literal_result.context.url) == f"https://{PUBLIC_IP}/story"


@pytest.mark.asyncio
async def test_fetch_follows_and_revalidates_public_redirects() -> None:
    calls: list[str] = []
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "start.example" and request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/next"})
        if request.url.host == "start.example" and request.url.path == "/next":
            return httpx.Response(301, headers={"location": "https://other.example/final"})
        if request.url.host == "other.example" and request.url.path == "/final":
            return html_response("<html><body><p>Final destination text.</p></body></html>")
        raise AssertionError(f"unexpected request: {request.url}")

    result = await run_fetch(
        make_fetcher(handler, public_resolver(calls)), "https://start.example/start"
    )
    assert result.failure is None
    context = result.context
    assert context is not None
    assert str(context.requested_url) == "https://start.example/start"
    assert str(context.url) == "https://other.example/final"
    assert context.publisher == "other.example"
    assert len(seen) == 3
    assert calls == ["start.example", "start.example", "other.example"]
    assert context.context_id.startswith("ctx-v1-")


@pytest.mark.asyncio
async def test_fetch_redirect_to_internal_or_non_http_is_blocked() -> None:
    targets = {
        "https://example.com/loop": "http://localhost/internal",
        "https://example.com/private": "http://10.0.0.1/internal",
        "https://example.com/file": "file:///etc/passwd",
    }
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        location = targets[str(request.url)]
        return httpx.Response(302, headers={"location": location})

    for url, expected in (
        ("https://example.com/loop", ResearchFailureCode.UNSAFE_URL),
        ("https://example.com/private", ResearchFailureCode.BLOCKED_NETWORK),
        ("https://example.com/file", ResearchFailureCode.UNSAFE_URL),
    ):
        result = await run_fetch(make_fetcher(handler), url)
        assert result.failure is not None
        assert result.failure.code is expected
    assert fetched == [
        "https://example.com/loop",
        "https://example.com/private",
        "https://example.com/file",
    ]


@pytest.mark.asyncio
async def test_fetch_redirect_limit_and_missing_location() -> None:
    def redirect_chain(request: httpx.Request) -> httpx.Response:
        hop = int(request.url.path.strip("/"))
        return httpx.Response(302, headers={"location": f"/{hop + 1}"})

    limited = await run_fetch(
        make_fetcher(redirect_chain, max_redirects=3), "https://example.com/0"
    )
    assert limited.failure is not None
    assert limited.failure.code is ResearchFailureCode.TOO_MANY_REDIRECTS

    def no_location(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302)

    missing = await run_fetch(make_fetcher(no_location), "https://example.com/start")
    assert missing.failure is not None
    assert missing.failure.code is ResearchFailureCode.FETCH_FAILED


@pytest.mark.asyncio
async def test_fetch_extra_context_redirect_must_stay_within_approved_hosts() -> None:
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://other.example/final"})
        return html_response("<html><body><p>final</p></body></html>")

    approved = frozenset({"example.com"})
    redirected = await run_fetch(
        make_fetcher(handler), "https://example.com/start", approved_hosts=approved
    )
    assert redirected.failure is not None
    assert redirected.failure.code is ResearchFailureCode.UNSAFE_URL
    assert fetched == ["https://example.com/start"]

    denied = await run_fetch(
        make_fetcher(explode), "https://other.example/final", approved_hosts=approved
    )
    assert denied.failure is not None
    assert denied.failure.code is ResearchFailureCode.UNSAFE_URL


@pytest.mark.asyncio
async def test_fetch_content_type_and_status_outcomes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/no-type":
            return httpx.Response(200, content=b"<html><body>text</body></html>")
        if path == "/pdf":
            return httpx.Response(
                200, content=b"%PDF-1.7 binary", headers={"content-type": "application/pdf"}
            )
        if path == "/gone":
            return httpx.Response(410)
        if path == "/missing":
            return httpx.Response(404)
        if path == "/forbidden":
            return httpx.Response(403)
        if path == "/plain":
            return httpx.Response(
                200,
                content=b"Plain text body.",
                headers={"content-type": "text/plain; charset=utf-8"},
            )
        if path == "/xhtml":
            return httpx.Response(
                200,
                content=b"<html><body><p>xhtml body</p></body></html>",
                headers={"content-type": "application/xhtml+xml"},
            )
        raise AssertionError(f"unexpected path {path}")

    fetcher = make_fetcher(handler)
    for path, expected in (
        ("/no-type", ResearchFailureCode.UNSUPPORTED_CONTENT_TYPE),
        ("/pdf", ResearchFailureCode.UNSUPPORTED_CONTENT_TYPE),
        ("/missing", ResearchFailureCode.MISSING_ARTICLE),
        ("/gone", ResearchFailureCode.MISSING_ARTICLE),
        ("/forbidden", ResearchFailureCode.FETCH_FAILED),
    ):
        result = await run_fetch(fetcher, f"https://example.com{path}")
        assert result.failure is not None, path
        assert result.failure.code is expected
    assert (await run_fetch(fetcher, "https://example.com/plain")).context is not None
    assert (await run_fetch(fetcher, "https://example.com/xhtml")).context is not None


@pytest.mark.asyncio
async def test_fetch_5xx_408_425_429_timeouts_and_transport_errors_propagate() -> None:
    def status_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(int(httpx.URL(str(request.url)).params["status"]))

    for status in (500, 502, 503, 408, 425, 429):
        with pytest.raises(httpx.HTTPStatusError):
            await run_fetch(
                make_fetcher(status_handler), f"https://example.com/story?status={status}"
            )

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(httpx.ReadTimeout):
        await run_fetch(make_fetcher(timeout_handler), "https://example.com/story")

    def connect_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(httpx.ConnectError):
        await run_fetch(make_fetcher(connect_handler), "https://example.com/story")

    dns = make_fetcher(explode, failing_resolver(socket.gaierror("no such host")))
    with pytest.raises(httpx.ConnectError):
        await run_fetch(dns, "https://example.com/story")


@pytest.mark.asyncio
async def test_fetch_oversized_response_is_truncated_and_bounded() -> None:
    body = "<html><body><p>" + ("word " * 4000) + "</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return html_response(body)

    byte_capped = await run_fetch(
        make_fetcher(handler, max_response_bytes=512), "https://example.com/big"
    )
    assert byte_capped.context is not None
    assert byte_capped.context.truncated is True
    assert len(byte_capped.context.text) <= MAX_CONTEXT_TEXT_CHARS
    assert ResearchNoteCode.RESPONSE_TRUNCATED in byte_capped.notes

    text_capped = await run_fetch(
        make_fetcher(handler, max_response_bytes=1_000_000), "https://example.com/big"
    )
    assert text_capped.context is not None
    assert len(text_capped.context.text) <= MAX_CONTEXT_TEXT_CHARS
    assert len(text_capped.context.text) > 7000
    assert text_capped.context.truncated is True
    assert ResearchNoteCode.RESPONSE_TRUNCATED in text_capped.notes


@pytest.mark.asyncio
async def test_fetch_script_only_or_empty_content_is_malformed() -> None:
    script_only = (FIXTURES / "article_malformed.html").read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/empty":
            return httpx.Response(200, content=b"   ", headers={"content-type": "text/plain"})
        return html_response(script_only)

    fetcher = make_fetcher(handler)
    for path in ("/script", "/empty"):
        result = await run_fetch(fetcher, f"https://example.com{path}")
        assert result.context is None
        assert result.failure is not None
        assert result.failure.code is ResearchFailureCode.MALFORMED_CONTENT


@pytest.mark.asyncio
async def test_make_research_client_disables_env_proxies_and_auto_redirects() -> None:
    client = make_research_client(load_settings({}))
    try:
        assert client.follow_redirects is False
        assert client.trust_env is False
        assert client.headers["accept-encoding"] == "identity"
        assert "test-agent" not in client.headers.get("user-agent", "")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_fetch_does_not_log_source_text_or_full_urls(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "SUPERSECRETBODYTOKEN"
    query_secret = "QUERYTOKEN42"

    def handler(request: httpx.Request) -> httpx.Response:
        return html_response(f"<html><body><p>{secret}</p></body></html>")

    with caplog.at_level(logging.DEBUG):
        result = await run_fetch(
            make_fetcher(handler), f"https://example.com/story?token={query_secret}"
        )
    assert result.context is not None
    assert secret in result.context.text
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert secret not in logged
    assert query_secret not in logged
    assert "https://example.com/story?token=" not in logged
