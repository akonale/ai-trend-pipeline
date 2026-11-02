"""Media contract validation, hashes and IDs."""

import re

import pytest
from pydantic import ValidationError

from ai_trend_pipeline.media.models import (
    ARTIFACT_ID_PATTERN,
    DrawImagesInput,
    DrawnArtifact,
    DrawnImagesResult,
    DrawnRole,
    DrawnStatus,
    IllustrationStatus,
    MediaArtifact,
    MediaKind,
    MediaManifest,
    MediaOutcome,
    MediaSelection,
    MediaType,
    artifact_id_for,
    drawn_artifact_id_for,
    media_brief_hash,
    with_media_choice,
)
from tests.media_fixtures import (
    HASH_A,
    HASH_B,
    artifact_payload,
    make_artifact,
    make_brief,
    make_manifest,
)


@pytest.mark.parametrize(
    "path",
    [
        "../x.png",
        "/etc/passwd",
        "a/b.png",
        "x.png",
        f"{HASH_A}.gif",
        f"{HASH_A}.png.exe",
        f"{HASH_B}.png",
        f"..\\{HASH_A}.png",
    ],
)
def test_artifact_rejects_unsafe_relative_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        MediaArtifact.model_validate(artifact_payload(relative_path=path))


@pytest.mark.parametrize(
    "overrides",
    [
        {"media_type": "image/svg+xml"},
        {"media_type": "text/html"},
        {"media_type": MediaType.GIF, "relative_path": f"{HASH_A}.gif"},
    ],
)
def test_artifact_rejects_disallowed_or_mismatched_mime(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MediaArtifact.model_validate(artifact_payload(**overrides))


def test_animated_png_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MediaArtifact.model_validate(
            artifact_payload(
                MediaKind.ANIMATED, media_type=MediaType.PNG, relative_path=f"{HASH_B}.png"
            )
        )


@pytest.mark.parametrize("alt", ["", "   \n", "x" * 1001])
def test_artifact_rejects_missing_or_oversized_alt_text(alt: str) -> None:
    with pytest.raises(ValidationError):
        MediaArtifact.model_validate(artifact_payload(alt_text=alt))


@pytest.mark.parametrize(
    ("kind", "overrides"),
    [
        (MediaKind.STATIC, {"width": 63}),
        (MediaKind.STATIC, {"height": 2049}),
        (MediaKind.STATIC, {"duration_ms": 2000}),
        (MediaKind.ANIMATED, {"duration_ms": None}),
        (MediaKind.ANIMATED, {"duration_ms": 999}),
        (MediaKind.ANIMATED, {"duration_ms": 6001}),
    ],
)
def test_artifact_rejects_invalid_dimensions_and_duration(
    kind: MediaKind, overrides: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        MediaArtifact.model_validate(artifact_payload(kind, **overrides))


def test_artifact_rejects_oversized_byte_size_per_kind() -> None:
    assert make_artifact(byte_size=1_000_000).byte_size == 1_000_000
    with pytest.raises(ValidationError):
        make_artifact(byte_size=1_000_001)
    assert make_artifact(MediaKind.ANIMATED, byte_size=4_000_000)
    with pytest.raises(ValidationError):
        make_artifact(MediaKind.ANIMATED, byte_size=4_000_001)
    with pytest.raises(ValidationError):
        make_artifact(byte_size=0)


def test_manifest_outcome_invariants() -> None:
    static = make_artifact(MediaKind.STATIC)
    animated = make_artifact(MediaKind.ANIMATED)
    for outcome in MediaOutcome:
        assert make_manifest(outcome).outcome is outcome
    bad: list[dict[str, object]] = [
        {"outcome": MediaOutcome.GIF, "animated_artifact": None},
        {"outcome": MediaOutcome.GIF, "static_artifact": None},
        {"outcome": MediaOutcome.STATIC, "animated_artifact": animated},
        {"outcome": MediaOutcome.STATIC, "static_artifact": None, "animated_artifact": None},
        {"outcome": MediaOutcome.STATIC_FALLBACK, "animated_artifact": animated},
        {"outcome": MediaOutcome.TEXT_ONLY, "static_artifact": static, "animated_artifact": None},
        {"outcome": MediaOutcome.GIF, "static_artifact": animated, "animated_artifact": static},
        {
            "outcome": MediaOutcome.GIF,
            "animated_artifact": make_artifact(MediaKind.ANIMATED, artifact_id=static.artifact_id),
        },
    ]
    for overrides in bad:
        payload: dict[str, object] = {
            "story_id": "s",
            "story_content_hash": HASH_A,
            "brief_hash": HASH_B,
            "static_artifact": static,
            "animated_artifact": animated,
        }
        payload.update(overrides)
        with pytest.raises(ValidationError):
            MediaManifest.model_validate(payload)


def test_manifest_artifact_lookup_uses_only_the_two_slots() -> None:
    manifest = make_manifest(MediaOutcome.GIF)
    assert manifest.static_artifact is not None
    assert manifest.artifact(manifest.static_artifact.artifact_id) == manifest.static_artifact
    assert manifest.artifact("art-v1-0000000000000000") is None


def test_manifest_rejects_oversized_json() -> None:
    big = "\U0001f600" * 1000  # 4 UTF-8 bytes each, so two artifacts exceed 8,192 bytes
    assert len(make_manifest(MediaOutcome.GIF).model_dump_json()) < 8_192
    with pytest.raises(ValidationError, match="too large"):
        make_manifest(
            MediaOutcome.GIF,
            static_artifact=make_artifact(MediaKind.STATIC, alt_text=big),
            animated_artifact=make_artifact(MediaKind.ANIMATED, alt_text=big),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"headline": "See https://example.com"},
        {"headline": "Visit www.example.com"},
        {"highlight": "a <b>bold</b> claim"},
        {"highlight": "a > b"},
        {"alt_text": "use `code`"},
        {"source_label": ""},
        {"unknown": "field"},
        {"animation_beats": []},
        {"highlight": None, "highlight_fact_ids": []},
    ],
)
def test_brief_rejects_urls_markup_and_extra_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_brief(**overrides)


def test_brief_collapses_whitespace_and_controls() -> None:
    assert make_brief(headline="  A \x00 b\n\tc ").headline == "A b c"


def test_identical_briefs_give_identical_artifact_ids_and_hashes() -> None:
    a, b = make_brief(), make_brief()
    assert media_brief_hash(a) == media_brief_hash(b)
    assert len(media_brief_hash(a)) == 64
    first = artifact_id_for(a, MediaKind.STATIC, "fake", "1")
    assert first == artifact_id_for(b, MediaKind.STATIC, "fake", "1")
    assert first.startswith("art-v1-") and len(first) == len("art-v1-") + 16


def test_artifact_id_changes_with_brief_kind_or_renderer_version() -> None:
    brief = make_brief()
    base = artifact_id_for(brief, MediaKind.STATIC, "fake", "1")
    assert base != artifact_id_for(make_brief(headline="Other"), MediaKind.STATIC, "fake", "1")
    assert base != artifact_id_for(brief, MediaKind.ANIMATED, "fake", "1")
    assert base != artifact_id_for(brief, MediaKind.STATIC, "fake", "2")
    assert base != artifact_id_for(brief, MediaKind.STATIC, "other", "1")


# --- 065a: illustration fields -------------------------------------------------------------

ILLUSTRATION_KEYS = {"illustration_artifact", "illustration_status", "selected"}
PRE_065_PACKAGE_HASH = "108f57c12e08b7f317e357cf8820262f041a21a5ba1734889daa5849a3df9a93"
PRE_065_GIF_PACKAGE_HASH = "855c28a843fe38403dc57e4b1f242ad0a19481c08ecc8057fee066a1977ef0cf"


def _illustration_artifact(**overrides: object) -> MediaArtifact:
    fields: dict[str, object] = {
        "artifact_id": "art-v1-" + "c" * 16,
        "content_hash": "c" * 64,
        "relative_path": f"{'c' * 64}.png",
    }
    fields.update(overrides)
    return MediaArtifact.model_validate(artifact_payload(MediaKind.STATIC, **fields))


def test_pre_065_manifest_dumps_without_the_new_keys() -> None:
    manifest = make_manifest(MediaOutcome.GIF)
    assert ILLUSTRATION_KEYS.isdisjoint(manifest.model_dump(mode="json"))
    assert ILLUSTRATION_KEYS.isdisjoint(manifest.model_dump())
    assert "illustration" not in manifest.model_dump_json()
    assert MediaManifest.model_validate_json(manifest.model_dump_json()) == manifest


def test_pinned_pre_065_package_hashes_are_unchanged() -> None:
    from ai_trend_pipeline.media.briefs import story_package_hash
    from tests.story_generation_fixtures import make_package

    assert story_package_hash(make_package("story-1")) == PRE_065_PACKAGE_HASH
    with_media = make_package("story-1", media=make_manifest(MediaOutcome.GIF))
    assert story_package_hash(with_media) == PRE_065_GIF_PACKAGE_HASH


def test_manifest_with_illustration_round_trips_and_is_served_by_artifact() -> None:
    from ai_trend_pipeline.media.models import IllustrationStatus

    illustration = _illustration_artifact()
    manifest = make_manifest(
        MediaOutcome.GIF,
        illustration_artifact=illustration,
        illustration_status=IllustrationStatus.MATCHED,
    )
    dumped = manifest.model_dump(mode="json")
    assert dumped["illustration_status"] == "matched"
    assert "selected" not in dumped
    assert MediaManifest.model_validate_json(manifest.model_dump_json()) == manifest
    assert manifest.artifact(illustration.artifact_id) == illustration


def test_illustration_status_without_artifact_is_allowed_only_for_failed_and_disabled() -> None:
    from ai_trend_pipeline.media.models import IllustrationStatus

    for status in (IllustrationStatus.FAILED, IllustrationStatus.DISABLED):
        manifest = make_manifest(MediaOutcome.STATIC, illustration_status=status)
        assert manifest.illustration_artifact is None
    for status in (IllustrationStatus.MATCHED, IllustrationStatus.GENERIC):
        with pytest.raises(ValidationError):
            make_manifest(MediaOutcome.STATIC, illustration_status=status)


def test_illustration_artifact_needs_matched_or_generic_status() -> None:
    from ai_trend_pipeline.media.models import IllustrationStatus

    for status in (None, IllustrationStatus.FAILED, IllustrationStatus.DISABLED):
        with pytest.raises(ValidationError):
            make_manifest(
                MediaOutcome.STATIC,
                illustration_artifact=_illustration_artifact(),
                illustration_status=status,
            )


def test_illustration_artifact_needs_a_card_and_must_be_static_with_a_distinct_id() -> None:
    from ai_trend_pipeline.media.models import IllustrationStatus

    good = {"illustration_status": IllustrationStatus.GENERIC}
    with pytest.raises(ValidationError):
        make_manifest(
            MediaOutcome.TEXT_ONLY, illustration_artifact=_illustration_artifact(), **good
        )
    with pytest.raises(ValidationError):
        make_manifest(
            MediaOutcome.STATIC,
            illustration_artifact=make_artifact(MediaKind.ANIMATED),
            **good,
        )
    card = make_artifact(MediaKind.STATIC)
    clash = _illustration_artifact(artifact_id=card.artifact_id)
    with pytest.raises(ValidationError):
        make_manifest(MediaOutcome.STATIC, illustration_artifact=clash, **good)


def test_selected_illustration_needs_an_artifact() -> None:
    from ai_trend_pipeline.media.models import IllustrationStatus, MediaSelection

    with pytest.raises(ValidationError):
        make_manifest(MediaOutcome.STATIC, selected=MediaSelection.ILLUSTRATION)
    manifest = make_manifest(MediaOutcome.STATIC, selected=MediaSelection.CARD)
    assert manifest.model_dump(mode="json")["selected"] == "card"
    ok = make_manifest(
        MediaOutcome.STATIC,
        illustration_artifact=_illustration_artifact(),
        illustration_status=IllustrationStatus.MATCHED,
        selected=MediaSelection.ILLUSTRATION,
    )
    assert ok.selected is MediaSelection.ILLUSTRATION


def test_illustration_artifact_ids_depend_on_input_renderer_and_version() -> None:
    from ai_trend_pipeline.media.models import IllustrateInput, illustration_artifact_id_for

    data = IllustrateInput(
        story_id="story-1", story_content_hash=HASH_A, headline="Headline", summary="Summary"
    )
    other = data.model_copy(update={"headline": "Other"})
    first = illustration_artifact_id_for(data, "illustrated-card", "1-abc")
    assert first.startswith("art-v1-") and len(first) == len("art-v1-") + 16
    assert first == illustration_artifact_id_for(data, "illustrated-card", "1-abc")
    assert first != illustration_artifact_id_for(other, "illustrated-card", "1-abc")
    assert first != illustration_artifact_id_for(data, "illustrated-card", "1-abd")
    assert first != illustration_artifact_id_for(data, "fake", "1-abc")
    card_id = artifact_id_for(make_brief(), MediaKind.STATIC, "illustrated-card", "1-abc")
    assert first != card_id


def test_illustrate_input_bounds_summary_and_hash() -> None:
    from ai_trend_pipeline.media.models import IllustrateInput

    with pytest.raises(ValidationError):
        IllustrateInput(story_id="s", story_content_hash="x", headline="H", summary="S")
    with pytest.raises(ValidationError):
        IllustrateInput(story_id="s", story_content_hash=HASH_A, headline="H", summary="x" * 601)
    with pytest.raises(ValidationError):
        IllustrateInput.model_validate(
            {"story_id": "s", "story_content_hash": HASH_A, "headline": "H", "summary": "", "x": 1}
        )


def test_illustration_result_artifact_only_for_matched_or_generic() -> None:
    from ai_trend_pipeline.media.models import IllustrationResult, IllustrationStatus

    artifact = _illustration_artifact()
    assert IllustrationResult(status=IllustrationStatus.MATCHED, artifact=artifact)
    for status in (IllustrationStatus.FAILED, IllustrationStatus.DISABLED):
        assert IllustrationResult(status=status).artifact is None
        with pytest.raises(ValidationError):
            IllustrationResult(status=status, artifact=artifact)
    with pytest.raises(ValidationError):
        IllustrationResult(status=IllustrationStatus.GENERIC)


def _illustrated(
    status: IllustrationStatus, outcome: MediaOutcome = MediaOutcome.GIF
) -> MediaManifest:
    return make_manifest(
        outcome, illustration_artifact=_illustration_artifact(), illustration_status=status
    )


def test_candidates_lists_illustration_first_and_card_last() -> None:
    manifest = _illustrated(IllustrationStatus.MATCHED)
    found = manifest.candidates()
    assert [c.selection for c in found] == [MediaSelection.ILLUSTRATION, MediaSelection.CARD]
    assert found[0].artifact == manifest.illustration_artifact
    assert found[0].animated is None
    assert found[1].artifact == manifest.static_artifact
    assert found[1].animated == manifest.animated_artifact


def test_candidates_preselect_follows_the_default_rule() -> None:
    matched = _illustrated(IllustrationStatus.MATCHED).candidates()
    assert [c.preselected for c in matched] == [True, False]
    generic = _illustrated(IllustrationStatus.GENERIC).candidates()
    assert [c.preselected for c in generic] == [False, True]


@pytest.mark.parametrize("status", [IllustrationStatus.FAILED, IllustrationStatus.DISABLED, None])
def test_candidates_without_an_illustration_is_only_the_preselected_card(
    status: IllustrationStatus | None,
) -> None:
    found = make_manifest(MediaOutcome.STATIC, illustration_status=status).candidates()
    assert [(c.selection, c.preselected) for c in found] == [(MediaSelection.CARD, True)]
    assert found[0].animated is None


def test_candidates_of_a_text_only_manifest_is_empty() -> None:
    assert make_manifest(MediaOutcome.TEXT_ONLY).candidates() == ()


def test_with_media_choice_sets_a_offered_choice_and_falls_back_to_card() -> None:
    manifest = _illustrated(IllustrationStatus.MATCHED)
    chosen = with_media_choice(manifest, MediaSelection.ILLUSTRATION)
    assert chosen.selected is MediaSelection.ILLUSTRATION
    assert with_media_choice(manifest, MediaSelection.CARD).selected is MediaSelection.CARD
    assert with_media_choice(manifest, None).selected is MediaSelection.CARD


def test_with_media_choice_is_unchanged_with_a_single_candidate() -> None:
    for manifest in (
        make_manifest(MediaOutcome.GIF),
        make_manifest(MediaOutcome.STATIC, illustration_status=IllustrationStatus.FAILED),
        make_manifest(MediaOutcome.TEXT_ONLY),
    ):
        assert with_media_choice(manifest, MediaSelection.ILLUSTRATION) is manifest
        assert with_media_choice(manifest, None).selected is None


def _drawn(role: DrawnRole, n: int = 0, **overrides: object) -> DrawnArtifact:
    digest = f"{n:x}" * 64
    artifact = make_artifact(
        artifact_id="art-v1-" + f"{n:016x}",
        content_hash=digest,
        relative_path=f"{digest}.png",
        **overrides,
    )
    return DrawnArtifact(role=role, artifact=artifact)


@pytest.mark.parametrize(
    "overrides",
    [
        {"media_type": MediaType.GIF, "relative_path": "a" * 64 + ".gif"},
        {"media_type": MediaType.WEBP, "relative_path": "a" * 64 + ".webp"},
        {"alt_text": "x" * 241},
    ],
)
def test_drawn_artifact_rejects(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DrawnArtifact(role=DrawnRole.HERO, artifact=make_artifact(**overrides))


def test_drawn_artifact_rejects_animated() -> None:
    with pytest.raises(ValidationError):
        DrawnArtifact(role=DrawnRole.HERO, artifact=make_artifact(MediaKind.ANIMATED))


def test_drawn_artifact_accepts_png_and_240_alt() -> None:
    assert _drawn(DrawnRole.HERO, alt_text="x" * 240).artifact.alt_text == "x" * 240


def test_drawn_images_result_rules() -> None:
    images = tuple(_drawn(role, n) for n, role in enumerate(DrawnRole, start=1))
    assert len(DrawnImagesResult(status=DrawnStatus.DRAWN, images=images).images) == 4
    for status in (DrawnStatus.FAILED, DrawnStatus.CAPPED, DrawnStatus.DISABLED):
        assert DrawnImagesResult(status=status).images == ()
        with pytest.raises(ValidationError):
            DrawnImagesResult(status=status, images=images[:1])
    with pytest.raises(ValidationError):
        DrawnImagesResult(status=DrawnStatus.DRAWN)
    with pytest.raises(ValidationError):  # duplicate role
        DrawnImagesResult(
            status=DrawnStatus.DRAWN, images=(_drawn(DrawnRole.HERO, 1), _drawn(DrawnRole.HERO, 2))
        )
    with pytest.raises(ValidationError):  # duplicate artifact id
        DrawnImagesResult(
            status=DrawnStatus.DRAWN,
            images=(_drawn(DrawnRole.HERO, 1), _drawn(DrawnRole.DETAIL, 1)),
        )
    with pytest.raises(ValidationError):  # more than 4
        DrawnImagesResult(
            status=DrawnStatus.DRAWN,
            images=(*images, _drawn(DrawnRole.HERO, 9)),
        )


def test_draw_images_input_bounds() -> None:
    ok = {"story_id": "s", "story_content_hash": HASH_A, "headline": "H", "summary": ""}
    assert DrawImagesInput.model_validate(ok).headline == "H"
    for bad in (
        {"story_id": ""},
        {"story_id": "x" * 65},
        {"story_content_hash": "zz"},
        {"headline": ""},
        {"headline": "x" * 161},
        {"summary": "x" * 601},
        {"extra": 1},
    ):
        with pytest.raises(ValidationError):
            DrawImagesInput.model_validate({**ok, **bad})


def test_drawn_artifact_id_for() -> None:
    args = (HASH_A, DrawnRole.HERO, HASH_B, "c" * 64, "claude-x")
    first = drawn_artifact_id_for(*args)
    assert first == drawn_artifact_id_for(*args)
    assert re.match(ARTIFACT_ID_PATTERN, first)
    variants = (
        (HASH_B, *args[1:]),
        (args[0], DrawnRole.DETAIL, *args[2:]),
        (*args[:2], HASH_A, *args[3:]),
        (*args[:3], "d" * 64, args[4]),
        (*args[:4], "claude-y"),
    )
    ids = {drawn_artifact_id_for(*v) for v in variants}
    assert first not in ids and len(ids) == 5
