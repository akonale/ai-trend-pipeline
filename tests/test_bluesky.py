"""Tests for the Bluesky posting activity against a stateful fake PDS (no live calls)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.bluesky import (
    MAX_IMAGE_BYTES,
    BlueskyActivities,
    BlueskyCredentials,
    PostChartInput,
    load_bluesky_credentials,
    make_tid,
)

FIXTURES = Path(__file__).parent / "fixtures"
CREATED = datetime(2026, 5, 1, 12, 0, 0, 123000, tzinfo=UTC)
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
TID_FIRST = "234567abcdefghij"


def fixture(name: str) -> dict[str, Any]:
    # Any: arbitrary JSON from a hand-written fixture file.
    return json.loads((FIXTURES / name).read_text())


class FakePds:
    """Records requests and stores created records by rkey."""

    def __init__(self, session_status: int = 200) -> None:
        self.session_status = session_status
        self.requests: list[httpx.Request] = []
        self.records: dict[str, dict[str, Any]] = {}  # Any: JSON record bodies

    def paths(self) -> list[str]:
        return [r.url.path.rsplit(".", 1)[-1] for r in self.requests]

    def bodies(self, name: str) -> list[Any]:  # Any: JSON bodies
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith(name)]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        name = request.url.path.rsplit(".", 1)[-1]
        did = fixture("bluesky_create_session.json")["did"]
        if name == "createSession":
            if self.session_status != 200:
                return httpx.Response(self.session_status, json={"error": "AuthenticationRequired"})
            return httpx.Response(200, json=fixture("bluesky_create_session.json"))
        if name == "getRecord":
            rkey = request.url.params["rkey"]
            if rkey in self.records:
                uri = f"at://{did}/app.bsky.feed.post/{rkey}"
                return httpx.Response(200, json={"uri": uri, "cid": "bafyexisting", "value": {}})
            return httpx.Response(400, json=fixture("bluesky_record_not_found.json"))
        if name == "uploadBlob":
            return httpx.Response(200, json=fixture("bluesky_upload_blob.json"))
        if name == "createRecord":
            body = json.loads(request.content)
            self.records[body["rkey"]] = body
            created = fixture("bluesky_create_record.json")
            created["uri"] = created["uri"].replace("RKEY", body["rkey"])
            return httpx.Response(200, json=created)
        return httpx.Response(404)


def make_activities(pds: FakePds) -> BlueskyActivities:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(pds.handler), base_url="https://pds.invalid/xrpc/"
    )
    creds = BlueskyCredentials.model_validate(
        {"handle": "test.example.invalid", "app_password": "pw-secret", "service": "https://x"}
    )
    return BlueskyActivities(client, creds)


def make_input(tmp_path: Path, data: bytes = PNG_BYTES, name: str = "chart.png") -> PostChartInput:
    png = tmp_path / name
    png.write_bytes(data)
    return PostChartInput(
        png_path=str(png),
        text="A caption",
        alt_text="Alt",
        idempotency_key="wf-1",
        created_at=CREATED,
    )


def test_make_tid_is_deterministic_and_valid() -> None:
    tid = make_tid(CREATED, "wf-1")
    assert tid == make_tid(CREATED, "wf-1")
    assert len(tid) == 13
    assert tid[0] in TID_FIRST
    assert set(tid) <= set("234567abcdefghijklmnopqrstuvwxyz")
    assert make_tid(CREATED, "wf-2") != tid
    assert make_tid(datetime(2026, 5, 2, tzinfo=UTC), "wf-1") != tid


@pytest.mark.asyncio
async def test_post_creates_record_with_image_embed(tmp_path: Path) -> None:
    pds = FakePds()
    data = make_input(tmp_path)
    out = await make_activities(pds).post_chart_to_bluesky(data)
    rkey = make_tid(CREATED, "wf-1")
    assert out.rkey == rkey
    assert out.already_posted is False
    assert out.uri.endswith(rkey)
    [body] = pds.bodies("createRecord")
    assert body["rkey"] == rkey
    assert body["collection"] == "app.bsky.feed.post"
    assert body["repo"] == fixture("bluesky_create_session.json")["did"]
    record = body["record"]
    assert record["text"] == "A caption"
    assert record["createdAt"] == "2026-05-01T12:00:00.123Z"
    assert record["embed"]["$type"] == "app.bsky.embed.images"
    assert record["embed"]["images"] == [
        {"image": fixture("bluesky_upload_blob.json")["blob"], "alt": "Alt"}
    ]
    upload = next(r for r in pds.requests if r.url.path.endswith("uploadBlob"))
    assert upload.content == PNG_BYTES
    assert upload.headers["content-type"] == "image/png"
    for req in pds.requests[1:]:
        assert req.headers["authorization"] == "Bearer fake.access.jwt"


@pytest.mark.asyncio
async def test_post_twice_posts_once(tmp_path: Path) -> None:
    pds = FakePds()
    acts = make_activities(pds)
    data = make_input(tmp_path)
    first = await acts.post_chart_to_bluesky(data)
    second = await acts.post_chart_to_bluesky(data)
    assert pds.paths().count("uploadBlob") == 1
    assert pds.paths().count("createRecord") == 1
    assert second.already_posted is True
    assert second.uri == first.uri
    assert second.rkey == first.rkey


@pytest.mark.asyncio
async def test_existing_record_skips_upload(tmp_path: Path) -> None:
    pds = FakePds()
    data = make_input(tmp_path)
    pds.records[make_tid(CREATED, "wf-1")] = {}
    out = await make_activities(pds).post_chart_to_bluesky(data)
    assert out.already_posted is True
    assert "uploadBlob" not in pds.paths()
    assert "createRecord" not in pds.paths()


@pytest.mark.asyncio
async def test_auth_failure_is_non_retryable(tmp_path: Path) -> None:
    pds = FakePds(session_status=401)
    with pytest.raises(ApplicationError) as exc:
        await make_activities(pds).post_chart_to_bluesky(make_input(tmp_path))
    assert exc.value.type == "BlueskyAuthFailed"
    assert exc.value.non_retryable


@pytest.mark.asyncio
async def test_image_too_large_is_non_retryable(tmp_path: Path) -> None:
    pds = FakePds()
    data = make_input(tmp_path, b"\x00" * (MAX_IMAGE_BYTES + 1))
    with pytest.raises(ApplicationError) as exc:
        await make_activities(pds).post_chart_to_bluesky(data)
    assert exc.value.type == "ImageTooLarge"
    assert exc.value.non_retryable
    assert "uploadBlob" not in pds.paths()


@pytest.mark.asyncio
async def test_missing_png_is_non_retryable(tmp_path: Path) -> None:
    pds = FakePds()
    data = PostChartInput(
        png_path=str(tmp_path / "nope.png"),
        text="t",
        idempotency_key="k",
        created_at=CREATED,
    )
    with pytest.raises(ApplicationError) as exc:
        await make_activities(pds).post_chart_to_bluesky(data)
    assert exc.value.type == "ChartFileMissing"
    assert exc.value.non_retryable


def test_load_credentials_missing_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLUESKY_HANDLE", "test.example.invalid")
    monkeypatch.delenv("BLUESKY_APP_PASSWORD", raising=False)
    monkeypatch.delenv("BLUESKY_SERVICE", raising=False)
    with pytest.raises(RuntimeError, match="BLUESKY_APP_PASSWORD"):
        load_bluesky_credentials()
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pw-secret")
    creds = load_bluesky_credentials()
    assert creds.service == "https://bsky.social"
    assert "pw-secret" not in repr(creds)


def test_post_input_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        PostChartInput(
            png_path="x.png", text="t", idempotency_key="k", created_at=datetime(2026, 1, 1)
        )
