"""Candidate use cases (start, list, show, decide) behind a gateway protocol."""

import base64
import binascii
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import (
    CandidateDecision,
    CandidateReviewState,
    CandidateStatus,
    DecidedByText,
    NoteText,
    StoryCandidate,
    StoryCandidateResult,
    candidate_id_for,
)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_CANDIDATE_ID_LENGTH = 128


class StartCandidateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=MAX_CANDIDATE_ID_LENGTH)


class ListCandidatesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    page_token: str | None = None

    @field_validator("page_token", mode="before")
    @classmethod
    def _blank_page_token(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class CandidateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=MAX_CANDIDATE_ID_LENGTH)
    source: SourceName
    title: str = Field(default="", max_length=200)
    proposed_angle: str = Field(default="", max_length=500)
    observed_at: datetime | None = None
    status: CandidateStatus


def _empty_summaries() -> list[CandidateSummary]:
    return []


class CandidatePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[CandidateSummary] = Field(
        default_factory=_empty_summaries, max_length=MAX_PAGE_SIZE
    )
    next_token: str | None = None


class DecideCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=MAX_CANDIDATE_ID_LENGTH)
    decision: Literal["approve", "reject"]
    note: NoteText = ""
    decided_by: DecidedByText | None = None


class CandidateServiceError(Exception):
    """Base class for candidate service errors."""


class CandidateNotFound(CandidateServiceError):
    def __init__(self, candidate_id: str) -> None:
        super().__init__(f"candidate {candidate_id} not found")
        self.candidate_id = candidate_id


class CandidateClosed(CandidateServiceError):
    def __init__(self, candidate_id: str, status: str) -> None:
        super().__init__(f"candidate {candidate_id} is not awaiting review (status: {status})")
        self.candidate_id = candidate_id
        self.status = status


class InvalidPageToken(CandidateServiceError):
    def __init__(self, token: str) -> None:
        super().__init__(f"invalid candidate page token: {token!r}")
        self.token = token


def encode_page_token(token: bytes) -> str:
    """URL-safe base64 without padding, suitable for a query string."""
    return base64.urlsafe_b64encode(token).decode("ascii").rstrip("=")


def decode_page_token(token: str) -> bytes:
    if not token:
        raise InvalidPageToken(token)
    padded = token + "=" * (-len(token) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidPageToken(token) from exc


class CandidateHandle(Protocol):
    @property
    def id(self) -> str: ...

    async def review_state(self) -> CandidateReviewState: ...

    async def approve(self, decision: CandidateDecision) -> None: ...

    async def reject(self, decision: CandidateDecision) -> None: ...

    async def result(self) -> StoryCandidateResult: ...


class CandidateGateway(Protocol):
    async def start(self, candidate: StoryCandidate, review_timeout_seconds: int) -> None: ...

    async def list_pending(self, request: ListCandidatesRequest) -> CandidatePage: ...

    def handle(self, candidate_id: str) -> CandidateHandle: ...


class CandidateService:
    def __init__(self, gateway: CandidateGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    async def start(
        self, candidate: StoryCandidate, review_timeout_seconds: int | None = None
    ) -> StartCandidateResult:
        timeout = (
            review_timeout_seconds
            if review_timeout_seconds is not None
            else self._settings.candidate_review_timeout_s
        )
        candidate_id = candidate_id_for(candidate.source, candidate.external_id)
        await self._gateway.start(candidate, timeout)
        return StartCandidateResult(candidate_id=candidate_id)

    async def list_pending(self, request: ListCandidatesRequest) -> CandidatePage:
        return await self._gateway.list_pending(request)

    async def show(self, candidate_id: str) -> CandidateReviewState:
        handle = self._gateway.handle(candidate_id)
        state = await handle.review_state()
        if state.status is not CandidateStatus.AWAITING_REVIEW:
            raise CandidateClosed(candidate_id, state.status)
        return state

    async def decide(self, request: DecideCandidateRequest) -> StoryCandidateResult:
        handle = self._gateway.handle(request.candidate_id)
        state = await handle.review_state()
        if state.status is not CandidateStatus.AWAITING_REVIEW:
            raise CandidateClosed(request.candidate_id, state.status)
        decision = CandidateDecision(note=request.note, decided_by=request.decided_by)
        try:
            if request.decision == "approve":
                await handle.approve(decision)
            else:
                await handle.reject(decision)
            return await handle.result()
        except CandidateNotFound as exc:
            raise CandidateClosed(request.candidate_id, "closed") from exc
