"""Builders for approved story packages with real media manifests."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ai_trend_pipeline.bluesky import BlueskyCredentials
from ai_trend_pipeline.media.briefs import story_content_hash, story_package_hash
from ai_trend_pipeline.media.models import (
    MediaArtifact,
    MediaKind,
    MediaManifest,
    MediaOutcome,
    MediaType,
    artifact_id_for,
    sha256_hex,
)
from ai_trend_pipeline.media.storage import write_artifact
from ai_trend_pipeline.publishing.bluesky_story import BlueskyStoryActivities
from ai_trend_pipeline.story_drafting.models import StoryPackage
from tests.drafting_fixtures import make_clean_package
from tests.media_fixtures import make_brief

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"static-card" * 4
GIF_BYTES = b"GIF89a" + b"animated-card" * 4


def make_real_artifact(
    kind: MediaKind, data: bytes, media_root: Path | None, alt_text: str = "Story card"
) -> MediaArtifact:
    media_type = MediaType.GIF if kind is MediaKind.ANIMATED else MediaType.PNG
    if media_root is not None:
        write_artifact(media_root, data, media_type)
    digest = sha256_hex(data)
    return MediaArtifact(
        artifact_id=artifact_id_for(make_brief(), kind, "fake", "1"),
        kind=kind,
        media_type=media_type,
        width=1200,
        height=675,
        duration_ms=4000 if kind is MediaKind.ANIMATED else None,
        byte_size=len(data),
        alt_text=alt_text,
        content_hash=digest,
        relative_path=f"{digest}.{'gif' if kind is MediaKind.ANIMATED else 'png'}",
    )


def make_approved_package(
    outcome: MediaOutcome | None = MediaOutcome.STATIC,
    *,
    media_root: Path | None = None,
    **overrides: object,
) -> tuple[StoryPackage, str]:
    """A package with a consistent manifest, and its approved package hash."""
    base = make_clean_package(**overrides)
    if outcome is None:
        return base, story_package_hash(base)
    static = (
        None
        if outcome is MediaOutcome.TEXT_ONLY
        else make_real_artifact(MediaKind.STATIC, PNG_BYTES, media_root)
    )
    animated = (
        make_real_artifact(MediaKind.ANIMATED, GIF_BYTES, media_root)
        if outcome is MediaOutcome.GIF
        else None
    )
    manifest = MediaManifest(
        story_id=base.story_id,
        story_content_hash=story_content_hash(base),
        brief_hash="b" * 64,
        outcome=outcome,
        static_artifact=static,
        animated_artifact=animated,
    )
    package = make_clean_package(media=manifest, **overrides)
    return package, story_package_hash(package)


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 5, 2, 9, 0, 0, tzinfo=UTC)
PASSWORD = "pw-secret"
DID = "did:plc:fakefakefakefakefakefake"


def fixture(name: str) -> dict[str, Any]:  # Any: hand-written JSON fixtures
    return json.loads((FIXTURES / name).read_text())


def story_fixture(name: str) -> dict[str, Any]:  # Any: hand-written JSON fixtures
    return fixture(f"bluesky_story/{name}")


Scripted = Callable[[httpx.Request], httpx.Response]


class FakePds:
    """Records requests, stores created records, and replays scripted responses per endpoint."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.records: dict[str, dict[str, Any]] = {}  # Any: JSON record bodies
        self.script: dict[str, list[Scripted]] = {}
        self.create_ok = 0

    def on(self, name: str, *responses: Scripted) -> None:
        self.script.setdefault(name, []).extend(responses)

    def names(self) -> list[str]:
        return [r.url.path.rsplit(".", 1)[-1] for r in self.requests]

    def bodies(self, name: str) -> list[Any]:  # Any: JSON bodies
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith(name)]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        name = request.url.path.rsplit(".", 1)[-1]
        queue = self.script.get(name)
        if queue:
            return queue.pop(0)(request)
        if name == "createSession":
            return httpx.Response(200, json=fixture("bluesky_create_session.json"))
        if name == "getRecord":
            rkey = request.url.params["rkey"]
            if rkey in self.records:
                found = story_fixture("get_record_found.json")
                found["uri"] = found["uri"].replace("RKEY", rkey)
                return httpx.Response(200, json=found)
            return httpx.Response(400, json=fixture("bluesky_record_not_found.json"))
        if name == "uploadBlob":
            return httpx.Response(200, json=fixture("bluesky_upload_blob.json"))
        if name == "createRecord":
            return self.create(request)
        return httpx.Response(404)

    def create(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["rkey"] in self.records:
            return httpx.Response(400, json=story_fixture("create_record_invalid.json"))
        self.records[body["rkey"]] = body
        self.create_ok += 1
        created = fixture("bluesky_create_record.json")
        created["uri"] = created["uri"].replace("RKEY", body["rkey"])
        return httpx.Response(200, json=created)


def respond(status: int, fixture_name: str | None = None, **headers: str) -> Scripted:
    def build(_: httpx.Request) -> httpx.Response:
        body = None if fixture_name is None else story_fixture(fixture_name)
        return httpx.Response(status, json=body, headers=headers)

    return build


def create_then_fail(pds: FakePds) -> Scripted:
    """The server stores the record but answers with a client error."""

    def build(request: httpx.Request) -> httpx.Response:
        pds.create(request)
        return httpx.Response(400, json=story_fixture("create_record_invalid.json"))

    return build


def transport_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("boom pw-secret", request=request)


def make_activities(
    pds: FakePds, media_root: Path, clock: Callable[[], datetime] = lambda: NOW
) -> BlueskyStoryActivities:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(pds.handler), base_url="https://pds.invalid/xrpc/"
    )
    creds = BlueskyCredentials.model_validate(
        {"handle": "test.example.invalid", "app_password": PASSWORD, "service": "https://x"}
    )
    return BlueskyStoryActivities(client, creds, media_root, clock=clock)
