"""Publish route and the Ready/Published panel with fake services; no Temporal."""

import html
import re
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.media.briefs import story_package_hash
from ai_trend_pipeline.media.models import MediaOutcome
from ai_trend_pipeline.publishing.models import (
    DestinationName,
    PublicationErrorCode,
    PublicationResult,
    PublishedMediaKind,
    publication_key,
)
from ai_trend_pipeline.services.publishing import (
    PublishStoryRequest,
    StoryNotPublishable,
    StoryPublishingService,
    StoryPublishStale,
)
from ai_trend_pipeline.services.stories import (
    StoryDetail,
    StoryDetailExpired,
    StoryDetailMalformed,
    StoryDetailNotFound,
    StoryDetailUnavailable,
)
from ai_trend_pipeline.story_generation import (
    StoryGenerationResult,
    StoryGenerationState,
    StoryGenerationStatus,
)
from ai_trend_pipeline.story_publishing import (
    FAILURE_DETAILS,
    PublicationStatus,
    StoryPublishingInput,
    StoryPublishingState,
)
from ai_trend_pipeline.story_status import PrimaryStoryStatus
from ai_trend_pipeline.web.app import create_app
from ai_trend_pipeline.web.dependencies import (
    CF_EMAIL_HEADER,
    get_story_detail_service,
    get_story_publishing_service,
)
from ai_trend_pipeline.web.view_models import (
    PUBLISH_FAILURE_HINTS,
    PUBLISH_NO_RETRY_HINT,
    STORY_PUBLISH_NOTICES,
    safe_bluesky_post_url,
)
from tests.test_web_story_detail import (
    CANDIDATE_ID,
    HASH,
    STORY_ID,
    FakeDetailService,
    make_package,
    media_detail,
)

POST_URL = "https://bsky.app/profile/did:plc:abcdefghijklmnopqrstuvwx/post/3kabcdefghijk"
SECRET = "app-password-SECRET-token"


def now() -> datetime:
    return datetime.now(UTC)


def state(
    status: PublicationStatus,
    detail: str | None = None,
    *,
    code: PublicationErrorCode | None = None,
    url: str = POST_URL,
) -> StoryPublishingState:
    result = None
    if status is PublicationStatus.PUBLISHED:
        result = PublicationResult(
            destination=DestinationName.BLUESKY,
            story_id=STORY_ID,
            candidate_id=CANDIDATE_ID,
            story_created_at=now(),
            content_hash="a" * 64,
            idempotency_key="k",
            uri="at://did:plc:abcdefghijklmnopqrstuvwx/app.bsky.feed.post/3kabcdefghijk",
            cid="bafy",
            rkey="3kabcdefghijk",
            public_url=url,
            published_at=now() - timedelta(minutes=4),
            media_kind=PublishedMediaKind.STATIC,
            already_published=False,
        )
    return StoryPublishingState(
        publication_id="publish-v1-x",
        destination=DestinationName.BLUESKY,
        status=status,
        status_detail=detail,
        error_code=code,
        requested_by="dev@localhost",
        publication=result,
    )


def ready_detail(
    publication: StoryPublishingState | None = None,
    *,
    unknown: bool = False,
    package_overrides: dict[str, object] | None = None,
) -> StoryDetail:
    package = make_package(**(package_overrides or {}))
    detail = media_detail(None, status=PrimaryStoryStatus.READY).model_copy(
        update={"package": package, "content_hash": story_package_hash(package)}
    )
    from tests.test_web_story_detail import safe_preview

    status = detail.status
    if publication is not None and publication.status is PublicationStatus.PUBLISHED:
        status = PrimaryStoryStatus.PUBLISHED
    return detail.model_copy(
        update={
            "publication": publication,
            "publication_unknown": unknown,
            "status": status,
            "post_preview": safe_preview(package),
        }
    )


class FakePublishService:
    def __init__(
        self, result: StoryPublishingState | None = None, error: Exception | None = None
    ) -> None:
        self.result = result
        self.error = error
        self.requests: list[PublishStoryRequest] = []

    async def publish(self, request: PublishStoryRequest, *, now: datetime) -> StoryPublishingState:
        _ = now
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def make_client(
    detail: StoryDetail | None = None,
    publisher: object | None = None,
    *,
    mode: str = "local",
) -> TestClient:
    app = create_app(Settings(auth_mode=mode))  # pyright: ignore[reportArgumentType]

    async def fake_detail() -> FakeDetailService:
        return FakeDetailService(detail)

    async def fake_publisher() -> object:
        return publisher

    app.dependency_overrides[get_story_detail_service] = fake_detail
    app.dependency_overrides[get_story_publishing_service] = fake_publisher
    return TestClient(app, follow_redirects=False)


def view(detail: StoryDetail) -> str:
    return html.unescape(make_client(detail).get(f"/stories/{STORY_ID}").text)


def badges(text: str) -> list[str]:
    return re.findall(r'<span class="badge (badge--(?!source)[a-z]+)">([^<]+)</span>', text)


def test_ready_story_shows_publish_form_with_content_hash() -> None:
    detail = ready_detail()
    text = view(detail)
    assert f'<form class="decision" method="post" action="/stories/{STORY_ID}/publish">' in text
    assert f'name="content_hash" value="{detail.content_hash}"' in text
    assert '<button class="btn btn--primary" type="submit">Publish to Bluesky</button>' in text
    assert "Nothing is posted until you press Publish." in text
    assert "hx-get" not in text
    assert badges(text) == [("badge--success", "Ready")]


def test_publish_post_redirects_started_and_calls_service_once_per_post() -> None:
    service = FakePublishService(state(PublicationStatus.PUBLISHING, "Posting to Bluesky."))
    client = make_client(ready_detail(), service)
    res = client.post(f"/stories/{STORY_ID}/publish", data={"content_hash": HASH})
    assert res.status_code == 303
    assert res.headers["location"] == f"/stories/{STORY_ID}?publish=started#story-notice"
    assert len(service.requests) == 1
    assert service.requests[0].requested_by == "dev@localhost"
    assert service.requests[0].detail_id == STORY_ID
    client.post(f"/stories/{STORY_ID}/publish", data={"content_hash": HASH})
    assert len(service.requests) == 2
    for status, token in (
        (PublicationStatus.PUBLISHED, "published"),
        (PublicationStatus.FAILED, "failed"),
    ):
        other = make_client(None, FakePublishService(state(status)))
        loc = other.post(f"/stories/{STORY_ID}/publish", data={"content_hash": HASH}).headers[
            "location"
        ]
        assert loc == f"/stories/{STORY_ID}?publish={token}#story-notice"


class CountingGateway:
    """A StoryPublishingGateway that counts starts, for the real service."""

    def __init__(self) -> None:
        package = make_package()
        self.package = package
        self.hash = story_package_hash(package)
        self.created = now() - timedelta(hours=2)
        self.starts: list[StoryPublishingInput] = []
        self.states: dict[str, StoryPublishingState] = {}

    async def story_state(self, workflow_id: str) -> StoryGenerationState:
        return StoryGenerationState(
            story_id=STORY_ID,
            candidate_id=CANDIDATE_ID,
            story_created_at=self.created,
            status=StoryGenerationStatus.READY,
        )

    async def story_result(self, workflow_id: str) -> StoryGenerationResult:
        return StoryGenerationResult(
            status="ready",
            story_id=STORY_ID,
            candidate_id=CANDIDATE_ID,
            story_created_at=self.created,
            updated_at=self.created,
            package=self.package,
            content_hash=self.hash,
        )

    async def publication(self, publication_id: str) -> StoryPublishingState | None:
        return self.states.get(publication_id)

    async def start(self, data: StoryPublishingInput) -> None:
        self.starts.append(data)
        key = publication_key(data.story_id, data.destination, data.content_hash)
        self.states[key] = StoryPublishingState(
            publication_id=key, status=PublicationStatus.PUBLISHING, status_detail="Posting."
        )


def test_double_post_returns_existing_state_without_second_start() -> None:
    gateway = CountingGateway()
    service = StoryPublishingService(gateway)
    client = make_client(None, service)
    for _ in range(3):
        res = client.post(f"/stories/{STORY_ID}/publish", data={"content_hash": gateway.hash})
        assert res.headers["location"].endswith("?publish=started#story-notice")
    assert len(gateway.starts) == 1


def test_publishing_state_shows_ready_badge_detail_disabled_button_and_polling() -> None:
    detail = ready_detail(state(PublicationStatus.PUBLISHING, "Posting to Bluesky."))
    text = view(detail)
    assert badges(text) == [("badge--success", "Ready")]
    assert '<span class="story-detail__progress">Posting to Bluesky.</span>' in text
    assert '<p role="status">Posting to Bluesky.</p>' in text
    assert '<button class="btn btn--primary" type="button" disabled>Publishing…</button>' in text
    assert "/publish" not in text.replace(f"/stories/{STORY_ID}/publish?", "")
    assert f'hx-get="/stories/{STORY_ID}/status"' in text
    assert "publishing</span>" not in text.lower().replace("publishing…", "")


def test_failed_state_shows_sanitized_sentence_hint_and_try_again() -> None:
    code = PublicationErrorCode.RATE_LIMITED
    detail = ready_detail(state(PublicationStatus.FAILED, SECRET, code=code))
    text = view(detail)
    assert "<h2>Not published</h2>" in text
    assert FAILURE_DETAILS[code] in text
    assert PUBLISH_FAILURE_HINTS[code] in text
    assert '<button class="btn btn--primary" type="submit">Try again</button>' in text
    assert f'action="/stories/{STORY_ID}/publish"' in text
    assert SECRET not in text
    assert badges(text) == [("badge--success", "Ready")]
    stopped = view(ready_detail(state(PublicationStatus.FAILED, "Publishing stopped. Try again.")))
    assert "Publishing stopped before it finished. Try again." in stopped
    assert "Try again</button>" in stopped


def test_package_problem_failure_has_no_try_again() -> None:
    for code in (
        PublicationErrorCode.CONTENT_HASH_MISMATCH,
        PublicationErrorCode.MANIFEST_MISMATCH,
        PublicationErrorCode.POST_TEXT_TOO_LONG,
        PublicationErrorCode.ALT_TEXT_TOO_LONG,
        PublicationErrorCode.MEDIA_NOT_SUPPORTED,
        PublicationErrorCode.MEDIA_TOO_LARGE,
    ):
        text = view(ready_detail(state(PublicationStatus.FAILED, code=code)))
        assert FAILURE_DETAILS[code] in text, code
        assert PUBLISH_NO_RETRY_HINT in text, code
        assert "Try again" not in text, code
        assert "<button" not in text, code
    for code in (PublicationErrorCode.MEDIA_MISSING, PublicationErrorCode.ARTIFACT_HASH_MISMATCH):
        text = view(ready_detail(state(PublicationStatus.FAILED, code=code)))
        assert "Restore the approved image file, then try again." in text
        assert "Try again</button>" in text


def test_blocked_preview_has_no_publish_button() -> None:
    text = view(ready_detail(package_overrides={"post_text": "x" * 290}))
    assert "It can't be published as approved." in text
    assert "Publish to Bluesky" not in text
    assert "<button" not in text
    assert "<form" not in text


def test_published_state_shows_single_published_badge_time_and_safe_link() -> None:
    text = view(ready_detail(state(PublicationStatus.PUBLISHED, "Published to Bluesky.")))
    assert badges(text) == [("badge--published", "Published")]
    assert "Published to Bluesky 4 minutes ago." in text
    assert f'href="{POST_URL}" target="_blank" rel="noopener noreferrer"' in text
    assert "View the post on Bluesky<span" in text
    assert " (opens in a new tab)" in text
    assert "story-rail--published" in text
    assert "This is the package that was published." in text
    assert "<button" not in text
    assert "at://" not in text
    assert "hx-get" not in text


def test_unsafe_public_url_renders_no_link() -> None:
    for url in (
        "javascript:alert(1)",
        "http://bsky.app/profile/x/post/y",
        "https://evil.example/profile/x/post/y",
        "https://bsky.app/profile/x/post/y?a=1",
        "https://bsky.app/profile/x/post/y#f",
        "https://bsky.app/profile//post/y",
        "https://bsky.app.evil.example/profile/x/post/y",
        "https://user@bsky.app/profile/x/post/y",
        "https://bsky.app/profile/x/post/",
    ):
        assert safe_bluesky_post_url(url) is None, url
        text = view(ready_detail(state(PublicationStatus.PUBLISHED, url=url)))
        assert "View the post on Bluesky" not in text, url
        assert "The post link could not be shown." in text
        assert "target=" not in text.split("story-rail")[1]
    assert safe_bluesky_post_url(POST_URL) == POST_URL


def test_stale_not_ready_and_malformed_hash_redirect_with_notices() -> None:
    cases = (
        (StoryPublishStale(STORY_ID), HASH, "stale"),
        (StoryNotPublishable(STORY_ID), HASH, "not_ready"),
    )
    for error, content_hash, token in cases:
        client = make_client(None, FakePublishService(error=error))
        res = client.post(f"/stories/{STORY_ID}/publish", data={"content_hash": content_hash})
        assert res.status_code == 303
        assert res.headers["location"] == f"/stories/{STORY_ID}?publish={token}#story-notice"
    real = StoryPublishingService(CountingGateway())
    for bad in ("", "not-a-hash", "G" * 64):
        res = make_client(None, real).post(
            f"/stories/{STORY_ID}/publish", data={"content_hash": bad}
        )
        assert res.headers["location"].endswith("?publish=stale#story-notice")
    notice = make_client(ready_detail()).get(f"/stories/{STORY_ID}?publish=stale").text
    assert html.unescape(notice).count(STORY_PUBLISH_NOTICES["stale"]) == 1


def test_missing_expired_malformed_unavailable_pages() -> None:
    for error, status in (
        (StoryDetailNotFound(STORY_ID), 404),
        (StoryDetailExpired(STORY_ID), 410),
        (StoryDetailMalformed(STORY_ID), 502),
        (StoryDetailUnavailable(), 503),
    ):
        client = make_client(None, FakePublishService(error=error))
        res = client.post(f"/stories/{STORY_ID}/publish", data={"content_hash": HASH})
        assert res.status_code == status
        assert "Back to stories" in res.text


def test_publish_requires_current_user_in_cloudflare_mode() -> None:
    service = FakePublishService(state(PublicationStatus.PUBLISHING))
    client = make_client(None, service, mode="cloudflare")
    assert (
        client.post(f"/stories/{STORY_ID}/publish", data={"content_hash": HASH}).status_code == 401
    )
    assert service.requests == []
    res = client.post(
        f"/stories/{STORY_ID}/publish",
        data={"content_hash": HASH},
        headers={CF_EMAIL_HEADER: "Editor@Example.com"},
    )
    assert res.status_code == 303
    assert service.requests[0].requested_by == "editor@example.com"


def test_publish_notice_allow_list_ignores_unknown_tokens() -> None:
    client = make_client(ready_detail())
    for token, message in STORY_PUBLISH_NOTICES.items():
        text = html.unescape(client.get(f"/stories/{STORY_ID}?publish={token}").text)
        assert message in text, token
    text = client.get(f"/stories/{STORY_ID}?publish=<script>alert(1)</script>").text
    assert "<script>alert(1)" not in text
    assert 'id="story-notice"' not in text or "alert" not in text


def test_failure_html_contains_no_post_text_token_or_raw_error() -> None:
    package_text = "Rates held steady again."
    failed = ready_detail(
        state(PublicationStatus.FAILED, f"{SECRET} {package_text} Traceback", code=None)
    )
    text = view(failed)
    panel = text[text.index("story-rail") :]
    assert SECRET not in panel
    assert "Traceback" not in text
    unknown = view(ready_detail(unknown=True))
    assert "Publishing status could not be read. Refresh to check again." in unknown
    assert "<button" not in unknown
    assert MediaOutcome.STATIC.value not in unknown.split("story-rail")[1]
