"""Activity: publish an approved story to Bluesky at most once per approved content hash.

Log-free on purpose. Every `ApplicationError` carries a fixed message, no details, and is raised
`from None`, so no response body, token, password or httpx text reaches Temporal history.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.bluesky import POST_COLLECTION, BlueskyCredentials, make_tid
from ai_trend_pipeline.media.models import MediaKind, has_media_signature, sha256_hex
from ai_trend_pipeline.media.storage import MediaArtifactMissing, resolve_artifact_path
from ai_trend_pipeline.publishing.formatter import PublicationRejected, format_publication
from ai_trend_pipeline.publishing.models import (
    RETRYABLE_CODES,
    DestinationCapabilities,
    PublicationErrorCode,
    PublicationInput,
    PublicationMedia,
    PublicationPackage,
    PublicationResult,
    PublishedMediaKind,
    publication_key,
)
from ai_trend_pipeline.publishing.registry import BLUESKY_CAPABILITIES

MIN_RETRY_DELAY = timedelta(seconds=1)
MAX_RETRY_DELAY = timedelta(minutes=15)

Code = PublicationErrorCode


def _fail(
    code: PublicationErrorCode, message: str, delay: timedelta | None = None
) -> ApplicationError:
    return ApplicationError(
        message,
        type=code.value,
        non_retryable=code not in RETRYABLE_CODES,
        next_retry_delay=delay,
    )


def _status_error(response: httpx.Response, clock: Callable[[], datetime]) -> ApplicationError:
    status = response.status_code
    if status == 429:
        return _fail(
            Code.RATE_LIMITED, "Destination rate limited (429)", _retry_delay(response, clock)
        )
    if status >= 500:
        return _fail(Code.UNAVAILABLE, f"Destination unavailable ({status})")
    return _fail(Code.UNEXPECTED_RESPONSE, f"Unexpected destination status ({status})")


def _retry_delay(response: httpx.Response, clock: Callable[[], datetime]) -> timedelta | None:
    header = response.headers.get("ratelimit-reset")
    if header is None:
        return None
    try:
        reset = float(header)
    except ValueError:
        return None
    delay = timedelta(seconds=reset - clock().timestamp())
    return max(MIN_RETRY_DELAY, min(MAX_RETRY_DELAY, delay))


def _as_dict(value: object) -> dict[str, Any] | None:  # Any: untyped PDS JSON
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _error_name(response: httpx.Response) -> str | None:
    try:
        body = _as_dict(response.json())
    except ValueError:
        return None
    name = None if body is None else body.get("error")
    return name if isinstance(name, str) else None


def _json(response: httpx.Response) -> dict[str, Any]:  # Any: untyped PDS JSON
    try:
        body = _as_dict(response.json())
    except ValueError:
        body = None
    if body is None:
        raise _fail(Code.UNEXPECTED_RESPONSE, "Unexpected destination response")
    return body


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise _fail(Code.UNEXPECTED_RESPONSE, "Unexpected destination response")
    return value


def _iso_z(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class BlueskyStoryActivities:
    def __init__(
        self,
        client: httpx.AsyncClient,
        credentials: BlueskyCredentials,
        media_root: Path,
        capabilities: DestinationCapabilities = BLUESKY_CAPABILITIES,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._credentials = credentials
        self._media_root = media_root
        self._capabilities = capabilities
        self._clock = clock

    async def _send(self, call: Awaitable[httpx.Response]) -> httpx.Response:
        try:
            return await call
        except httpx.TransportError:
            raise _fail(Code.UNAVAILABLE, "Destination unreachable") from None

    async def _load_media(self, publication: PublicationPackage) -> bytes | None:
        media = publication.media
        if media is None:
            return None
        artifact = media.artifact
        try:
            path = resolve_artifact_path(self._media_root, artifact)
            data = await asyncio.to_thread(path.read_bytes)
        except (MediaArtifactMissing, OSError):
            raise _fail(Code.MEDIA_MISSING, "Approved media file is missing") from None
        if sha256_hex(data) != artifact.content_hash or not has_media_signature(
            data, artifact.media_type
        ):
            raise _fail(Code.ARTIFACT_HASH_MISMATCH, "Media file does not match its hash")
        return data

    async def _session(self) -> tuple[str, str]:
        response = await self._send(
            self._client.post(
                "com.atproto.server.createSession",
                json={
                    "identifier": self._credentials.handle,
                    "password": self._credentials.app_password.get_secret_value(),
                },
            )
        )
        if response.status_code == 401:
            raise _fail(Code.AUTH_FAILED, "Destination rejected the credentials (401)")
        if response.status_code != 200:
            raise _status_error(response, self._clock)
        body = _json(response)
        return _text(body, "accessJwt"), _text(body, "did")

    async def _get_record(self, auth: dict[str, str], did: str, rkey: str) -> httpx.Response | None:
        """The found record response, or None when the record does not exist."""
        response = await self._send(
            self._client.get(
                "com.atproto.repo.getRecord",
                params={"repo": did, "collection": POST_COLLECTION, "rkey": rkey},
                headers=auth,
            )
        )
        if response.status_code == 200:
            return response
        if response.status_code == 400 and _error_name(response) == "RecordNotFound":
            return None
        if response.status_code == 401:
            raise _fail(Code.AUTH_FAILED, "Destination rejected the credentials (401)")
        raise _status_error(response, self._clock)

    async def _upload(self, auth: dict[str, str], media: PublicationMedia, data: bytes) -> Any:  # noqa: ANN401  # Any: opaque blob ref echoed back to the PDS
        response = await self._send(
            self._client.post(
                "com.atproto.repo.uploadBlob",
                content=data,
                headers={**auth, "Content-Type": media.artifact.media_type.value},
            )
        )
        if response.status_code == 200:
            blob = _as_dict(_json(response).get("blob"))
            if blob is None:
                raise _fail(Code.UNEXPECTED_RESPONSE, "Unexpected destination response")
            return blob
        if response.status_code == 401:
            raise _fail(Code.AUTH_FAILED, "Destination rejected the credentials (401)")
        if response.status_code == 413 or _error_name(response) == "BlobTooLarge":
            raise _fail(Code.MEDIA_TOO_LARGE, "Destination rejected the media size")
        raise _status_error(response, self._clock)

    def _record(
        self,
        data: PublicationInput,
        publication: PublicationPackage,
        blob: Any,  # noqa: ANN401  # Any: opaque blob ref
    ) -> dict[str, Any]:  # Any: JSON record body
        record: dict[str, Any] = {
            "$type": POST_COLLECTION,
            "text": publication.text,
            "createdAt": _iso_z(data.requested_at),
        }
        if publication.facets:
            record["facets"] = [
                {
                    "$type": "app.bsky.richtext.facet",
                    "index": {"byteStart": facet.byte_start, "byteEnd": facet.byte_end},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": str(facet.uri)}],
                }
                for facet in publication.facets
            ]
        if publication.media is not None:
            artifact = publication.media.artifact
            record["embed"] = {
                "$type": "app.bsky.embed.images",
                "images": [
                    {
                        "image": blob,
                        "alt": publication.media.alt_text,
                        "aspectRatio": {"width": artifact.width, "height": artifact.height},
                    }
                ],
            }
        return record

    def _result(
        self,
        data: PublicationInput,
        publication: PublicationPackage,
        *,
        key: str,
        did: str,
        rkey: str,
        uri: str,
        cid: str,
        published_at: datetime,
        already: bool,
    ) -> PublicationResult:
        media = publication.media
        kind = PublishedMediaKind.NONE
        if media is not None:
            kind = (
                PublishedMediaKind.ANIMATED
                if media.artifact.kind is MediaKind.ANIMATED
                else PublishedMediaKind.STATIC
            )
        return PublicationResult(
            destination=data.destination,
            story_id=data.story_id,
            candidate_id=data.candidate_id,
            story_created_at=data.story_created_at,
            content_hash=data.content_hash,
            idempotency_key=key,
            uri=uri,
            cid=cid,
            rkey=rkey,
            public_url=f"https://bsky.app/profile/{did}/post/{rkey}",
            published_at=published_at,
            media_kind=kind,
            artifact_id=None if media is None else media.artifact.artifact_id,
            already_published=already,
        )

    def _found(
        self,
        found: httpx.Response,
        data: PublicationInput,
        publication: PublicationPackage,
        key: str,
        did: str,
        rkey: str,
    ) -> PublicationResult:
        body = _json(found)
        published_at = data.requested_at
        value = _as_dict(body.get("value"))
        created = None if value is None else value.get("createdAt")
        if isinstance(created, str):
            try:
                parsed = datetime.fromisoformat(created)
            except ValueError:
                parsed = None
            if parsed is not None and parsed.utcoffset() is not None:
                published_at = parsed
        return self._result(
            data,
            publication,
            key=key,
            did=did,
            rkey=rkey,
            uri=_text(body, "uri"),
            cid=_text(body, "cid"),
            published_at=published_at,
            already=True,
        )

    @activity.defn
    async def publish_story_to_bluesky(self, data: PublicationInput) -> PublicationResult:
        try:
            publication = format_publication(data.package, data.content_hash, self._capabilities)
        except PublicationRejected as exc:
            raise _fail(exc.code, str(exc)) from None
        blob_bytes = await self._load_media(publication)

        key = publication_key(data.story_id, data.destination, data.content_hash)
        rkey = make_tid(data.story_created_at, key)
        token, did = await self._session()
        auth = {"Authorization": f"Bearer {token}"}

        existing = await self._get_record(auth, did, rkey)
        if existing is not None:
            return self._found(existing, data, publication, key, did, rkey)

        blob: Any = None
        if publication.media is not None and blob_bytes is not None:
            blob = await self._upload(auth, publication.media, blob_bytes)

        response = await self._send(
            self._client.post(
                "com.atproto.repo.createRecord",
                json={
                    "repo": did,
                    "collection": POST_COLLECTION,
                    "rkey": rkey,
                    "record": self._record(data, publication, blob),
                },
                headers=auth,
            )
        )
        status = response.status_code
        if status == 200:
            body = _json(response)
            return self._result(
                data,
                publication,
                key=key,
                did=did,
                rkey=rkey,
                uri=_text(body, "uri"),
                cid=_text(body, "cid"),
                published_at=data.requested_at,
                already=False,
            )
        if status == 401:
            raise _fail(Code.AUTH_FAILED, "Destination rejected the credentials (401)")
        if status == 413:
            raise _fail(Code.MEDIA_TOO_LARGE, "Destination rejected the media size")
        if status == 429 or status >= 500 or not 400 <= status < 500:
            raise _status_error(response, self._clock)
        recheck = await self._get_record(auth, did, rkey)
        if recheck is not None:
            return self._found(recheck, data, publication, key, did, rkey)
        raise _fail(Code.REJECTED, f"Destination rejected the post ({status})")
