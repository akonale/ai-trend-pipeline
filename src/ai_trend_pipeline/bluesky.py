"""Activity: post an approved chart to Bluesky over plain XRPC, exactly once per input."""

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, SecretStr, field_validator
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.config import Settings

MAX_IMAGE_BYTES = 1_000_000
POST_COLLECTION = "app.bsky.feed.post"
TID_ALPHABET = "234567abcdefghijklmnopqrstuvwxyz"
TID_LENGTH = 13


class PostChartInput(BaseModel):
    png_path: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=300)
    alt_text: str = Field(default="", max_length=1000)
    idempotency_key: str = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class PostChartOutput(BaseModel):
    uri: str
    cid: str
    rkey: str
    already_posted: bool


class BlueskyCredentials(BaseModel):
    handle: str
    app_password: SecretStr
    service: str


def load_bluesky_credentials(settings: Settings) -> BlueskyCredentials:
    missing = [
        name
        for name, value in (
            ("BLUESKY_HANDLE", settings.bluesky_handle),
            ("BLUESKY_APP_PASSWORD", settings.bluesky_app_password),
        )
        if value is None
    ]
    if missing or settings.bluesky_handle is None or settings.bluesky_app_password is None:
        raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing)}")
    return BlueskyCredentials(
        handle=settings.bluesky_handle,
        app_password=settings.bluesky_app_password,
        service=settings.bluesky_service,
    )


def make_bluesky_client(service: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=f"{service.rstrip('/')}/xrpc/", timeout=30.0)


def make_tid(created_at: datetime, idempotency_key: str) -> str:
    micros = int(created_at.timestamp() * 1_000_000)
    clock_id = int.from_bytes(sha256(idempotency_key.encode()).digest()[:2]) & 0x3FF
    value = (micros << 10) | clock_id
    chars = [TID_ALPHABET[(value >> shift) & 0x1F] for shift in range(5 * (TID_LENGTH - 1), -1, -5)]
    return "".join(chars)


def _read_png(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except FileNotFoundError as exc:
        raise ApplicationError(
            f"Chart file not found: {path}", type="ChartFileMissing", non_retryable=True
        ) from exc


def _iso_z(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class BlueskyActivities:
    def __init__(self, client: httpx.AsyncClient, credentials: BlueskyCredentials):
        self._client = client
        self._credentials = credentials

    async def _create_session(self) -> tuple[str, str]:
        response = await self._client.post(
            "com.atproto.server.createSession",
            json={
                "identifier": self._credentials.handle,
                "password": self._credentials.app_password.get_secret_value(),
            },
        )
        if response.status_code == 401:
            raise ApplicationError(
                "Bluesky rejected the credentials", type="BlueskyAuthFailed", non_retryable=True
            )
        response.raise_for_status()
        body = response.json()
        return body["accessJwt"], body["did"]

    @activity.defn
    async def post_chart_to_bluesky(self, data: PostChartInput) -> PostChartOutput:
        png = await asyncio.to_thread(_read_png, data.png_path)
        if len(png) > MAX_IMAGE_BYTES:
            raise ApplicationError(
                f"PNG is {len(png)} bytes, over the {MAX_IMAGE_BYTES} limit",
                type="ImageTooLarge",
                non_retryable=True,
            )
        rkey = make_tid(data.created_at, data.idempotency_key)
        token, did = await self._create_session()
        auth = {"Authorization": f"Bearer {token}"}

        existing = await self._client.get(
            "com.atproto.repo.getRecord",
            params={"repo": did, "collection": POST_COLLECTION, "rkey": rkey},
            headers=auth,
        )
        if existing.status_code == 200:
            found = existing.json()
            return PostChartOutput(
                uri=found["uri"], cid=found["cid"], rkey=rkey, already_posted=True
            )
        if existing.status_code != 400 or existing.json().get("error") != "RecordNotFound":
            existing.raise_for_status()
            raise ApplicationError(f"Unexpected getRecord status {existing.status_code}")

        uploaded = await self._client.post(
            "com.atproto.repo.uploadBlob",
            content=png,
            headers={**auth, "Content-Type": "image/png"},
        )
        uploaded.raise_for_status()
        blob: Any = uploaded.json()["blob"]  # Any: opaque blob ref echoed back to the PDS

        created = await self._client.post(
            "com.atproto.repo.createRecord",
            json={
                "repo": did,
                "collection": POST_COLLECTION,
                "rkey": rkey,
                "record": {
                    "$type": POST_COLLECTION,
                    "text": data.text,
                    "createdAt": _iso_z(data.created_at),
                    "embed": {
                        "$type": "app.bsky.embed.images",
                        "images": [{"image": blob, "alt": data.alt_text}],
                    },
                },
            },
            headers=auth,
        )
        created.raise_for_status()
        result = created.json()
        return PostChartOutput(
            uri=result["uri"], cid=result["cid"], rkey=rkey, already_posted=False
        )
