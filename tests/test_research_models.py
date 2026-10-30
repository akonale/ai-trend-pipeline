"""Contract tests for the research models: bounds, freezing, invariants and stable IDs."""

import json
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from ai_trend_pipeline.research import (
    MAX_CONTEXT_TEXT_CHARS,
    MAX_DATASET_ROWS,
    MAX_EVIDENCE,
    MAX_FAILURES,
    MAX_HOST_CHARS,
    MAX_NOTES,
    MAX_RESEARCH_OUTPUT_BYTES_TARGET,
    MAX_TOTAL_CONTEXT_CHARS,
    DatasetEvidence,
    DatasetRow,
    EvidenceKind,
    EvidenceSource,
    FetchContextInput,
    HostName,
    ResearchFailure,
    ResearchFailureCode,
    ResearchInput,
    ResearchNoteCode,
    ResearchOutput,
    ResearchStatus,
    StoryContext,
    StoryInput,
    context_id_for,
    evidence_id_for,
)
from ai_trend_pipeline.sources import SourceName

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
HOST = TypeAdapter(HostName)


def make_story(**overrides: object) -> StoryInput:
    data: dict[str, object] = {
        "candidate_id": "candidate-v1-0123456789abcdef",
        "source": SourceName.HN,
        "external_id": "hn-1",
        "title": "Life expectancy keeps rising",
        "url": "https://news.example/story",
        "published_at": NOW - timedelta(hours=2),
        "observed_at": NOW,
        "summary": "A summary of the story.",
        "proposed_angle": "Show the long-run trend.",
        "selection_reason": "Relevant and timely.",
        "metrics": {"score": 10},
    }
    data.update(overrides)
    return StoryInput.model_validate(data)


def make_context(**overrides: object) -> StoryContext:
    data: dict[str, object] = {
        "context_id": context_id_for("https://news.example/story"),
        "requested_url": "https://news.example/story",
        "url": "https://news.example/story",
        "title": "Life expectancy keeps rising",
        "publisher": "news.example",
        "content_type": "text/html",
        "text": "x" * 400,
        "truncated": False,
        "retrieved_at": NOW,
    }
    data.update(overrides)
    return StoryContext.model_validate(data)


def make_dataset(**overrides: object) -> DatasetEvidence:
    data: dict[str, object] = {
        "slug": "life-expectancy",
        "title": "Life expectancy",
        "value_column": "life_expectancy_0",
        "since_year": 2000,
        "rows": [DatasetRow(entity="World", code="OWID_WRL", year=2020, value=72.5)],
        "matched_countries": [],
        "notes": [],
        "truncated": False,
    }
    data.update(overrides)
    return DatasetEvidence.model_validate(data)


def make_evidence(**overrides: object) -> EvidenceSource:
    data: dict[str, object] = {
        "evidence_id": evidence_id_for(EvidenceKind.ARTICLE, "ctx-v1-0123456789abcdef"),
        "kind": EvidenceKind.ARTICLE,
        "title": "Life expectancy keeps rising",
        "url": "https://news.example/story",
        "publisher": "news.example",
        "retrieved_at": NOW,
        "observation_date": date(2026, 10, 30),
        "context_id": "ctx-v1-0123456789abcdef",
        "excerpt": "A short excerpt.",
        "dataset": None,
    }
    data.update(overrides)
    return EvidenceSource.model_validate(data)


def make_dataset_evidence(**overrides: object) -> EvidenceSource:
    dataset = make_dataset()
    data: dict[str, object] = {
        "evidence_id": evidence_id_for(EvidenceKind.DATASET, dataset.slug),
        "kind": EvidenceKind.DATASET,
        "title": dataset.title,
        "url": None,
        "publisher": None,
        "retrieved_at": NOW,
        "observation_date": None,
        "context_id": None,
        "excerpt": None,
        "dataset": dataset,
    }
    data.update(overrides)
    return EvidenceSource.model_validate(data)


def make_output(**overrides: object) -> ResearchOutput:
    context = make_context()
    data: dict[str, object] = {
        "story_id": "candidate-v1-0123456789abcdef",
        "status": ResearchStatus.SUPPORTED,
        "contexts": [context],
        "evidence": [make_evidence(context_id=context.context_id)],
        "failures": [],
        "notes": [],
        "researched_at": NOW,
    }
    data.update(overrides)
    return ResearchOutput.model_validate(data)


def test_story_input_bounds_and_rejects_non_http_urls() -> None:
    story = make_story(url=None, published_at=None)
    assert story.url is None
    assert story.title == "Life expectancy keeps rising"
    assert make_story(title="  padded  ").title == "padded"

    with pytest.raises(ValidationError):
        make_story(title="")
    with pytest.raises(ValidationError):
        make_story(title="t" * 201)
    with pytest.raises(ValidationError):
        make_story(summary="")
    with pytest.raises(ValidationError):
        make_story(proposed_angle="a" * 501)
    with pytest.raises(ValidationError):
        make_story(selection_reason=" ")
    for bad_url in ("javascript:alert(1)", "ftp://example.com/a", "file:///etc/passwd"):
        with pytest.raises(ValidationError):
            make_story(url=bad_url)
    with pytest.raises(ValidationError):
        make_story(unknown_field="nope")
    with pytest.raises(ValidationError):
        story.title = "changed"


def test_context_dataset_and_evidence_models_are_bounded_and_frozen() -> None:
    with pytest.raises(ValidationError):
        DatasetRow(entity="World", code=None, year=1799, value=1.0)
    with pytest.raises(ValidationError):
        DatasetRow(entity="World", code=None, year=2101, value=1.0)
    with pytest.raises(ValidationError):
        DatasetRow(entity="World", code=None, year=2000, value=float("inf"))

    rows = [DatasetRow(entity=f"Country {i}", code=None, year=2000, value=1.0) for i in range(301)]
    with pytest.raises(ValidationError):
        make_dataset(rows=rows)
    assert len(make_dataset(rows=rows[:MAX_DATASET_ROWS]).rows) == MAX_DATASET_ROWS
    with pytest.raises(ValidationError):
        make_dataset(matched_countries=[f"Country {i}" for i in range(21)])
    with pytest.raises(ValidationError):
        make_dataset(notes=[f"note {i}" for i in range(6)])
    with pytest.raises(ValidationError):
        make_dataset(extra=True)

    with pytest.raises(ValidationError):
        make_context(text="x" * (MAX_CONTEXT_TEXT_CHARS + 1))
    with pytest.raises(ValidationError):
        make_context(title="t" * 301)
    with pytest.raises(ValidationError):
        make_context(extra="nope")
    with pytest.raises(ValidationError):
        make_evidence(excerpt="e" * 501)
    with pytest.raises(ValidationError):
        make_dataset_evidence(extra=True)

    with pytest.raises(ValidationError):
        make_context(context_id="ctx").context_id = "other"


def test_evidence_kind_consistency_and_stable_ids() -> None:
    with pytest.raises(ValidationError):
        make_evidence(context_id=None)
    with pytest.raises(ValidationError):
        make_evidence(dataset=make_dataset())
    with pytest.raises(ValidationError):
        make_dataset_evidence(dataset=None)
    with pytest.raises(ValidationError):
        make_dataset_evidence(context_id="ctx-v1-0123456789abcdef")

    first = context_id_for("https://news.example/story")
    assert first == context_id_for("https://news.example/story")
    assert first.startswith("ctx-v1-") and len(first) == len("ctx-v1-") + 16
    assert first != context_id_for("https://news.example/other")

    dataset = evidence_id_for(EvidenceKind.DATASET, "life-expectancy")
    assert dataset == evidence_id_for(EvidenceKind.DATASET, "life-expectancy")
    assert dataset.startswith("ev-v1-") and len(dataset) == len("ev-v1-") + 16
    assert dataset != evidence_id_for(EvidenceKind.DATASET, "gdp-per-capita-worldbank")
    assert dataset != evidence_id_for(EvidenceKind.ARTICLE, "life-expectancy")


def test_research_input_limits_extra_urls_and_validates_allowlist_hosts() -> None:
    urls = [f"https://context{i}.example/a" for i in range(2)]
    data = ResearchInput.model_validate({"story": make_story(), "extra_context_urls": urls})
    assert data.include_owid is True
    assert len(data.extra_context_urls) == 2

    with pytest.raises(ValidationError):
        ResearchInput.model_validate(
            {"story": make_story(), "extra_context_urls": [*urls, "https://context2.example/a"]}
        )
    with pytest.raises(ValidationError):
        ResearchInput.model_validate(
            {"story": make_story(), "extra_context_urls": [urls[0], urls[0]]}
        )
    with pytest.raises(ValidationError):
        ResearchInput.model_validate({"story": make_story(), "include_owid": None})

    assert HOST.validate_python("Example.COM") == "example.com"
    assert HOST.validate_python("sub.example.co.uk") == "sub.example.co.uk"
    long_host = ".".join(["a" * 63] * 3 + ["a" * 61])
    assert len(long_host) == MAX_HOST_CHARS
    assert HOST.validate_python(long_host) == long_host
    for bad in (
        "https://example.com",
        "example.com:443",
        "example.com/path",
        "user@example.com",
        "127.0.0.1",
        "::1",
        "localhost",
        "example",
        "foo.local",
        "foo.internal",
        "foo.home.arpa",
        "foo.lan",
        "-bad.example",
        "bad-.example",
        ".example.com",
        "example.com.",
    ):
        with pytest.raises(ValidationError):
            HOST.validate_python(bad)
    with pytest.raises(ValidationError):
        HOST.validate_python("a" * 254 + ".com")

    allowed = FetchContextInput.model_validate(
        {
            "url": "https://news.example/story",
            "approved_hosts": frozenset({"news.example", "context.example"}),
        }
    )
    assert allowed.approved_hosts == frozenset({"news.example", "context.example"})
    assert (
        FetchContextInput.model_validate({"url": "https://news.example/story"}).approved_hosts
        is None
    )


def test_research_output_supported_requires_qualifying_article_and_linked_contexts() -> None:
    short_context = make_context(text="x" * 299)
    with pytest.raises(ValidationError):
        make_output(
            contexts=[short_context], evidence=[make_evidence(context_id=short_context.context_id)]
        )

    context = make_context()
    assert make_output(contexts=[context], evidence=[]).status is ResearchStatus.SUPPORTED

    with pytest.raises(ValidationError):
        make_output(
            contexts=[context],
            evidence=[make_evidence(context_id="ctx-v1-ffffffffffffffff")],
        )
    with pytest.raises(ValidationError):
        make_output(
            contexts=[context],
            evidence=[
                make_evidence(context_id=context.context_id),
                make_evidence(context_id=context.context_id),
            ],
        )

    supported = make_output()
    assert supported.status is ResearchStatus.SUPPORTED

    unsupported = make_output(
        status=ResearchStatus.UNSUPPORTED,
        contexts=[],
        evidence=[],
        failures=[ResearchFailure(code=ResearchFailureCode.MISSING_ARTICLE, message="missing")],
    )
    assert unsupported.status is ResearchStatus.UNSUPPORTED

    with pytest.raises(ValidationError):
        make_output(status=ResearchStatus.UNSUPPORTED, failures=[])


def test_research_output_enforces_total_text_and_list_caps() -> None:
    contexts = [
        make_context(
            context_id=context_id_for(f"https://news.example/{i}"),
            text="x" * MAX_CONTEXT_TEXT_CHARS,
        )
        for i in range(4)
    ]
    with pytest.raises(ValidationError):
        make_output(contexts=contexts)

    two = contexts[:2]
    evidence = [make_evidence(context_id=c.context_id) for c in two]
    with pytest.raises(ValidationError):
        make_output(
            contexts=two, evidence=evidence, notes=[ResearchNoteCode.RESPONSE_TRUNCATED] * 9
        )
    with pytest.raises(ValidationError):
        make_output(
            contexts=two,
            evidence=[
                *evidence,
                make_dataset_evidence(),
                make_dataset_evidence(),
                make_dataset_evidence(),
            ],
        )
    with pytest.raises(ValidationError):
        make_output(
            contexts=two,
            evidence=evidence,
            failures=[
                ResearchFailure(code=ResearchFailureCode.FETCH_FAILED, message=f"f{i}")
                for i in range(MAX_FAILURES + 1)
            ],
        )

    three_texts = ["x" * 8000, "x" * 7000, "x" * 1000]
    total_contexts = [
        make_context(context_id=context_id_for(f"https://news.example/t{i}"), text=text)
        for i, text in enumerate(three_texts)
    ]
    assert sum(len(c.text) for c in total_contexts) == MAX_TOTAL_CONTEXT_CHARS
    max_rows = [
        DatasetRow(entity=f"Entity {i}", code=f"C{i}", year=2020, value=float(i))
        for i in range(300)
    ]
    near_max = make_output(
        contexts=total_contexts,
        evidence=[
            *(
                make_evidence(
                    evidence_id=evidence_id_for(EvidenceKind.ARTICLE, c.context_id),
                    context_id=c.context_id,
                    excerpt="e" * 500,
                )
                for c in total_contexts
            ),
            make_dataset_evidence(dataset=make_dataset(rows=max_rows, truncated=True)),
        ],
        failures=[
            ResearchFailure(code=ResearchFailureCode.FETCH_FAILED, message=f"failure {i}")
            for i in range(MAX_FAILURES)
        ],
        notes=[ResearchNoteCode.RESPONSE_TRUNCATED] * MAX_NOTES,
    )
    payload = near_max.model_dump_json()
    assert len(payload.encode()) < MAX_RESEARCH_OUTPUT_BYTES_TARGET
    assert len(near_max.evidence) <= MAX_EVIDENCE


def test_research_failure_sanitizes_message_and_keeps_host_only() -> None:
    failure = ResearchFailure(
        code=ResearchFailureCode.FETCH_FAILED,
        message="bad \x00\x01\x07host\nnow",
        host="Example.COM",
    )
    assert "\x00" not in failure.message
    assert "\x07" not in failure.message
    assert failure.message == "bad hostnow"
    assert failure.host == "example.com"
    assert (
        len(ResearchFailure(code=ResearchFailureCode.FETCH_FAILED, message="m" * 200).message)
        == 200
    )
    with pytest.raises(ValidationError):
        ResearchFailure(code=ResearchFailureCode.FETCH_FAILED, message="m" * 201)
    with pytest.raises(ValidationError):
        ResearchFailure(
            code=ResearchFailureCode.FETCH_FAILED,
            message="bad",
            host="https://example.com/path?q=secret",
        )
    with pytest.raises(ValidationError):
        ResearchFailure(code=ResearchFailureCode.FETCH_FAILED, message=" \x00 ")
    with pytest.raises(ValidationError):
        ResearchFailure.model_validate(
            {"code": ResearchFailureCode.FETCH_FAILED, "message": "bad", "extra": "nope"}
        )


def test_datetimes_are_aware_utc_and_models_round_trip_json() -> None:
    with pytest.raises(ValidationError):
        make_story(observed_at=datetime(2026, 10, 30, 12, 0))
    with pytest.raises(ValidationError):
        make_context(retrieved_at=datetime(2026, 10, 30, 12, 0))
    shifted = make_story(
        observed_at=datetime(2026, 10, 30, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    )
    assert shifted.observed_at == NOW
    assert shifted.observed_at.utcoffset() == timedelta(0)

    output = make_output()
    dumped = output.model_dump_json()
    assert ResearchOutput.model_validate_json(dumped) == output
    parsed = json.loads(dumped)
    assert parsed["researched_at"].endswith("Z") or parsed["researched_at"].endswith("+00:00")
