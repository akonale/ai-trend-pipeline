"""Story publication activity against a stateful fake PDS (no live calls)."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.bluesky import make_tid
from ai_trend_pipeline.media.models import MediaOutcome
from ai_trend_pipeline.publishing.bluesky_story import BlueskyStoryActivities
from ai_trend_pipeline.publishing.models import (
    RETRYABLE_CODES,
    DestinationName,
    PublicationErrorCode,
    PublicationInput,
    PublishedMediaKind,
    publication_key,
)
from tests.publishing_fixtures import (
    DID,
    GIF_BYTES,
    NOW,
    PASSWORD,
    PNG_BYTES,
    FakePds,
    Scripted,
    create_then_fail,
    fixture,
    make_activities,
    make_approved_package,
    respond,
    transport_error,
)

CREATED = datetime(2026, 5, 1, 12, 0, 0, 123000, tzinfo=UTC)
REQUESTED = datetime(2026, 5, 2, 8, 30, 15, 500000, tzinfo=UTC)


def make_input(
    media_root: Path, outcome: MediaOutcome | None = MediaOutcome.STATIC, **overrides: object
) -> PublicationInput:
    package, digest = make_approved_package(outcome, media_root=media_root, **overrides)
    return PublicationInput(
        destination=DestinationName.BLUESKY,
        story_id=package.story_id,
        candidate_id="cand-1",
        story_created_at=CREATED,
        requested_at=REQUESTED,
        package=package,
        content_hash=digest,
    )


async def failure(activities: BlueskyStoryActivities, data: PublicationInput) -> ApplicationError:
    with pytest.raises(ApplicationError) as info:
        await activities.publish_story_to_bluesky(data)
    return info.value


@pytest.mark.asyncio
async def test_text_only_post_has_facets_and_no_embed(tmp_path: Path) -> None:
    pds = FakePds()
    data = make_input(tmp_path, None)
    result = await make_activities(pds, tmp_path).publish_story_to_bluesky(data)
    assert pds.names() == ["createSession", "getRecord", "createRecord"]
    key = publication_key(data.story_id, DestinationName.BLUESKY, data.content_hash)
    rkey = make_tid(CREATED, key)
    (body,) = pds.bodies("createRecord")
    record = body["record"]
    assert body["rkey"] == rkey and body["repo"] == DID
    assert record["text"].endswith("Source: news.example")
    assert record["createdAt"] == "2026-05-02T08:30:15.500Z"
    assert "embed" not in record
    (facet,) = record["facets"]
    assert facet["$type"] == "app.bsky.richtext.facet"
    assert facet["features"] == [
        {"$type": "app.bsky.richtext.facet#link", "uri": "https://news.example/story"}
    ]
    raw = record["text"].encode()
    assert raw[facet["index"]["byteStart"] : facet["index"]["byteEnd"]] == b"news.example"
    assert result.media_kind is PublishedMediaKind.NONE
    assert result.artifact_id is None
    assert not result.already_published
    assert result.idempotency_key == key
    assert result.rkey == rkey
    assert result.public_url == f"https://bsky.app/profile/{DID}/post/{rkey}"
    assert result.published_at == REQUESTED


@pytest.mark.asyncio
async def test_image_post_uploads_png_with_alt_and_aspect_ratio(tmp_path: Path) -> None:
    pds = FakePds()
    data = make_input(tmp_path)
    result = await make_activities(pds, tmp_path).publish_story_to_bluesky(data)
    assert pds.names() == ["createSession", "getRecord", "uploadBlob", "createRecord"]
    upload = next(r for r in pds.requests if r.url.path.endswith("uploadBlob"))
    assert upload.content == PNG_BYTES
    assert upload.headers["content-type"] == "image/png"
    assert upload.headers["authorization"] == "Bearer fake.access.jwt"
    embed = pds.bodies("createRecord")[0]["record"]["embed"]
    assert embed["$type"] == "app.bsky.embed.images"
    (image,) = embed["images"]
    assert image["alt"] == "Story card"
    assert image["aspectRatio"] == {"width": 1200, "height": 675}
    assert image["image"] == fixture("bluesky_upload_blob.json")["blob"]
    assert result.media_kind is PublishedMediaKind.STATIC
    assert result.artifact_id is not None


@pytest.mark.asyncio
async def test_gif_story_uploads_static_fallback_not_gif(tmp_path: Path) -> None:
    pds = FakePds()
    data = make_input(tmp_path, MediaOutcome.GIF)
    result = await make_activities(pds, tmp_path).publish_story_to_bluesky(data)
    uploads = [r for r in pds.requests if r.url.path.endswith("uploadBlob")]
    assert [u.content for u in uploads] == [PNG_BYTES]
    assert GIF_BYTES not in [u.content for u in uploads]
    assert result.media_kind is PublishedMediaKind.STATIC


@pytest.mark.asyncio
async def test_existing_record_returns_already_published_without_upload_or_create(
    tmp_path: Path,
) -> None:
    pds = FakePds()
    data = make_input(tmp_path)
    key = publication_key(data.story_id, DestinationName.BLUESKY, data.content_hash)
    rkey = make_tid(CREATED, key)
    pds.records[rkey] = {}
    result = await make_activities(pds, tmp_path).publish_story_to_bluesky(data)
    assert pds.names() == ["createSession", "getRecord"]
    assert result.already_published
    assert result.rkey == rkey
    assert result.uri.endswith(rkey)
    assert result.cid == "bafyreiexistingexistingexisting"
    assert result.published_at == datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_upload_failure_is_retryable_then_second_call_posts_once(tmp_path: Path) -> None:
    pds = FakePds()
    pds.on("uploadBlob", respond(503), transport_error)
    activities = make_activities(pds, tmp_path)
    data = make_input(tmp_path)
    first = await failure(activities, data)
    second = await failure(activities, data)
    assert first.type == PublicationErrorCode.UNAVAILABLE.value and not first.non_retryable
    assert second.type == PublicationErrorCode.UNAVAILABLE.value and not second.non_retryable
    assert pds.records == {}
    result = await activities.publish_story_to_bluesky(data)
    assert pds.create_ok == 1 and not result.already_published


@pytest.mark.asyncio
async def test_create_failure_after_server_created_record_recovers_as_already_published(
    tmp_path: Path,
) -> None:
    pds = FakePds()
    pds.on("createRecord", create_then_fail(pds))
    data = make_input(tmp_path)
    result = await make_activities(pds, tmp_path).publish_story_to_bluesky(data)
    assert result.already_published
    assert pds.names().count("getRecord") == 2
    assert pds.create_ok == 1
    assert len(pds.records) == 1


@pytest.mark.asyncio
async def test_create_5xx_is_retryable_and_retry_posts_once(tmp_path: Path) -> None:
    pds = FakePds()
    pds.on("createRecord", respond(502))
    activities = make_activities(pds, tmp_path)
    data = make_input(tmp_path, None)
    error = await failure(activities, data)
    assert error.type == PublicationErrorCode.UNAVAILABLE.value and not error.non_retryable
    result = await activities.publish_story_to_bluesky(data)
    assert not result.already_published and pds.create_ok == 1


@pytest.mark.asyncio
async def test_sequential_and_concurrent_duplicate_calls_create_one_record(
    tmp_path: Path,
) -> None:
    pds = FakePds()
    activities = make_activities(pds, tmp_path)
    data = make_input(tmp_path)
    first = await activities.publish_story_to_bluesky(data)
    second = await activities.publish_story_to_bluesky(data)
    assert (first.uri, first.rkey) == (second.uri, second.rkey)
    assert not first.already_published and second.already_published
    assert pds.create_ok == 1 and len(pds.records) == 1

    pds2 = FakePds()
    activities2 = make_activities(pds2, tmp_path)
    one, two = await asyncio.gather(
        activities2.publish_story_to_bluesky(data), activities2.publish_story_to_bluesky(data)
    )
    assert (one.uri, one.rkey) == (two.uri, two.rkey)
    assert sorted([one.already_published, two.already_published]) == [False, True]
    assert pds2.create_ok == 1 and len(pds2.records) == 1


@pytest.mark.asyncio
async def test_rate_limit_is_retryable_with_bounded_next_retry_delay(tmp_path: Path) -> None:
    data = make_input(tmp_path, None)

    async def delay_for(reset: str | None) -> ApplicationError:
        pds = FakePds()
        headers = {} if reset is None else {"ratelimit-reset": reset}
        pds.on("getRecord", respond(429, "rate_limited.json", **headers))
        return await failure(make_activities(pds, tmp_path), data)

    now = NOW.timestamp()
    error = await delay_for(str(int(now) + 120))
    assert error.type == PublicationErrorCode.RATE_LIMITED.value
    assert not error.non_retryable
    assert error.next_retry_delay == timedelta(seconds=120)
    assert (await delay_for(str(int(now) - 500))).next_retry_delay == timedelta(seconds=1)
    assert (await delay_for(str(int(now) + 10_000))).next_retry_delay == timedelta(minutes=15)
    assert (await delay_for(None)).next_retry_delay is None
    assert (await delay_for("soon")).next_retry_delay is None

    pds = FakePds()
    pds.on("uploadBlob", respond(429, "rate_limited.json"))
    upload_error = await failure(make_activities(pds, tmp_path), make_input(tmp_path))
    assert upload_error.type == PublicationErrorCode.RATE_LIMITED.value


@pytest.mark.asyncio
async def test_auth_failure_is_non_retryable(tmp_path: Path) -> None:
    pds = FakePds()
    pds.on("createSession", respond(401, "auth_failed.json"))
    error = await failure(make_activities(pds, tmp_path), make_input(tmp_path))
    assert error.type == PublicationErrorCode.AUTH_FAILED.value
    assert error.non_retryable
    assert pds.names() == ["createSession"]


@pytest.mark.asyncio
async def test_destination_rejected_post_is_non_retryable(tmp_path: Path) -> None:
    pds = FakePds()
    pds.on("createRecord", respond(400, "create_record_invalid.json"))
    error = await failure(make_activities(pds, tmp_path), make_input(tmp_path, None))
    assert error.type == PublicationErrorCode.REJECTED.value
    assert error.non_retryable
    assert pds.names().count("getRecord") == 2

    pds = FakePds()
    pds.on("uploadBlob", respond(400, "upload_blob_too_large.json"))
    too_large = await failure(make_activities(pds, tmp_path), make_input(tmp_path))
    assert too_large.type == PublicationErrorCode.MEDIA_TOO_LARGE.value
    assert too_large.non_retryable

    pds = FakePds()
    pds.on("uploadBlob", respond(413))
    assert (await failure(make_activities(pds, tmp_path), make_input(tmp_path))).non_retryable


@pytest.mark.asyncio
async def test_unexpected_responses_are_retryable(tmp_path: Path) -> None:
    pds = FakePds()
    pds.on("getRecord", respond(418))
    error = await failure(make_activities(pds, tmp_path), make_input(tmp_path, None))
    assert error.type == PublicationErrorCode.UNEXPECTED_RESPONSE.value
    assert not error.non_retryable

    pds = FakePds()
    pds.on("createSession", lambda _: httpx.Response(200, text="not json"))
    bad_json = await failure(make_activities(pds, tmp_path), make_input(tmp_path, None))
    assert bad_json.type == PublicationErrorCode.UNEXPECTED_RESPONSE.value


@pytest.mark.asyncio
async def test_hash_mismatch_and_bad_media_make_no_http_requests(tmp_path: Path) -> None:
    pds = FakePds()
    activities = make_activities(pds, tmp_path)
    data = make_input(tmp_path)
    stale = data.model_copy(update={"content_hash": "d" * 64})
    assert (await failure(activities, stale)).type == PublicationErrorCode.CONTENT_HASH_MISMATCH
    long_text = make_input(tmp_path, None, post_text="x" * 295)
    too_long = await failure(activities, long_text)
    assert too_long.type == PublicationErrorCode.POST_TEXT_TOO_LONG.value
    assert too_long.non_retryable
    capped = BlueskyStoryActivities(
        activities._client,  # pyright: ignore[reportPrivateUsage]
        activities._credentials,  # pyright: ignore[reportPrivateUsage]
        tmp_path,
        capabilities=activities._capabilities.model_copy(  # pyright: ignore[reportPrivateUsage]
            update={"max_static_bytes": 5}
        ),
    )
    assert (await failure(capped, data)).type == PublicationErrorCode.MEDIA_TOO_LARGE.value
    assert pds.requests == []


@pytest.mark.asyncio
async def test_missing_or_tampered_artifact_raises_before_network(tmp_path: Path) -> None:
    pds = FakePds()
    data = make_input(tmp_path)
    artifact = data.package.media.static_artifact  # pyright: ignore[reportOptionalMemberAccess]
    assert artifact is not None
    path = tmp_path / artifact.relative_path
    activities = make_activities(pds, tmp_path)

    tampered = bytes([PNG_BYTES[0]]) + b"X" * (len(PNG_BYTES) - 1)
    path.write_bytes(tampered)
    error = await failure(activities, data)
    assert error.type == PublicationErrorCode.ARTIFACT_HASH_MISMATCH.value
    assert error.non_retryable

    path.write_bytes(PNG_BYTES[:-1])
    assert (await failure(activities, data)).type == PublicationErrorCode.MEDIA_MISSING.value

    path.unlink()
    missing = await failure(activities, data)
    assert missing.type == PublicationErrorCode.MEDIA_MISSING.value
    assert missing.non_retryable
    assert pds.requests == []


@pytest.mark.asyncio
async def test_errors_and_logs_contain_no_secrets_or_raw_responses(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    scenarios: list[tuple[str, Scripted]] = [
        ("createSession", respond(401, "auth_failed.json")),
        ("createSession", transport_error),
        ("getRecord", respond(429, "rate_limited.json")),
        ("getRecord", respond(500, "create_record_invalid.json")),
        ("uploadBlob", respond(400, "upload_blob_too_large.json")),
        ("createRecord", respond(400, "create_record_invalid.json")),
    ]
    raw_fragments = ["Invalid identifier", "Rate Limit Exceeded", "too large", "text is invalid"]
    for endpoint, script in scenarios:
        pds = FakePds()
        pds.on(endpoint, script)
        error = await failure(make_activities(pds, tmp_path), make_input(tmp_path))
        shown = f"{error!s} {error.details!r} {error.type}"
        assert error.details == ()
        assert error.__cause__ is None
        for secret in (PASSWORD, "fake.access.jwt", "fake.refresh.jwt", *raw_fragments):
            assert secret not in shown
            assert secret not in caplog.text
        assert error.non_retryable == (
            PublicationErrorCode(error.type or "") not in RETRYABLE_CODES
        )
    assert caplog.records == [] or all("pw-secret" not in r.getMessage() for r in caplog.records)
