"""Recorded TypeSafe answers replayed for workflow tests that need a working classifier."""

import json
from pathlib import Path
from typing import cast

import httpx2
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from ai_trend_pipeline.story_classification import StoryClassificationActivities

POSITIVE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "typesafe_story_classification_positive.json"
)
TEST_API_KEY = "sentinel-typesafe-key"


def positive_typesafe_client() -> AsyncTypeSafeClient:
    """A client whose transport answers every event asked with the recorded positive answers."""
    recorded = cast(dict[str, object], json.loads(POSITIVE_FIXTURE.read_text(encoding="utf-8")))
    template = cast(dict[str, dict[str, object]], recorded["answers"])

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method != "POST" or request.url.path != "/v1/systemone":
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        body = cast(dict[str, dict[str, object]], json.loads(request.content))
        answers = {
            f"{key}__{name.split('__', 1)[1]}": answer
            for key in cast(dict[str, object], body["state"]["events"])
            for name, answer in template.items()
        }
        return httpx2.Response(200, json={**recorded, "answers": answers})

    return AsyncTypeSafeClient(
        api_key=TEST_API_KEY,
        model="jev-1.14.0",
        retry=RetryPolicy(max_retries=0),
        transport=httpx2.MockTransport(handler),
        compression=False,
    )


def positive_classification() -> StoryClassificationActivities:
    """Real classification activity; every event is judged an eligible, clear human story."""
    return StoryClassificationActivities(positive_typesafe_client(), "jev-1.14.0")
