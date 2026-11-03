"""Reduced-motion media view: poster for everyone, GIF only when motion is fine."""

import html
import re

import pytest

from ai_trend_pipeline.media.briefs import story_package_hash
from ai_trend_pipeline.media.models import (
    IllustrationStatus,
    MediaArtifact,
    MediaCandidate,
    MediaKind,
    MediaManifest,
    MediaOutcome,
    MediaSelection,
)
from ai_trend_pipeline.story_status import PrimaryStoryStatus
from ai_trend_pipeline.web.view_models import (
    PICK_LEGEND,
    PICK_RAIL_NOTE,
    build_story_detail_view,
    build_story_media_view,
    build_story_pick_view,
)
from tests.media_fixtures import make_artifact, make_manifest
from tests.test_web_story_detail import (
    NOW,
    STORY_ID,
    FakeDetailService,
    client_for,
    make_detail,
    make_package,
)


def test_gif_manifest_uses_poster_as_img_and_gif_only_for_motion_ok() -> None:
    manifest = make_manifest(MediaOutcome.GIF)
    view = build_story_media_view("story-1", manifest)
    assert view is not None
    assert manifest.static_artifact is not None and manifest.animated_artifact is not None
    assert view.poster_src.endswith(manifest.static_artifact.artifact_id)
    assert view.animated_src is not None
    assert view.animated_src.endswith(manifest.animated_artifact.artifact_id)
    assert view.motion_media == "(prefers-reduced-motion: no-preference)"
    assert (view.poster_width, view.poster_height) == (1200, 675)
    assert view.alt_text == manifest.static_artifact.alt_text


def test_static_and_static_fallback_have_no_animated_src() -> None:
    for outcome in (MediaOutcome.STATIC, MediaOutcome.STATIC_FALLBACK):
        view = build_story_media_view("story-1", make_manifest(outcome))
        assert view is not None and view.animated_src is None


def test_text_only_or_missing_manifest_gives_none() -> None:
    assert build_story_media_view("story-1", None) is None
    assert build_story_media_view("story-1", make_manifest(MediaOutcome.TEXT_ONLY)) is None


def test_urls_are_built_from_manifest_ids_and_quoted_detail_id() -> None:
    manifest = make_manifest(MediaOutcome.GIF)
    view = build_story_media_view("a b/c?x", manifest)
    assert view is not None and manifest.static_artifact is not None
    assert view.poster_src == f"/stories/a%20b%2Fc%3Fx/media/{manifest.static_artifact.artifact_id}"


def test_kind_text_and_motion_note_per_outcome() -> None:
    gif = build_story_media_view("story-1", make_manifest(MediaOutcome.GIF))
    assert gif is not None
    assert gif.kind_text == "Animated card. Plays once, 4 seconds."
    assert gif.motion_note == "Reduced motion is on, so the still card is shown."
    assert gif.alt_label == "Alt text:"
    static = build_story_media_view("story-1", make_manifest(MediaOutcome.STATIC))
    assert static is not None
    assert static.kind_text == "Still card."
    assert static.motion_note == ""
    fallback = build_story_media_view("story-1", make_manifest(MediaOutcome.STATIC_FALLBACK))
    assert fallback is not None
    assert fallback.kind_text == (
        "Still card. The animation could not be made, so this still card is used."
    )
    assert fallback.motion_note == ""


def test_failed_illustration_adds_the_fixed_note_and_keeps_the_card() -> None:
    failed = make_manifest(MediaOutcome.GIF, illustration_status=IllustrationStatus.FAILED)
    view = build_story_media_view("story-1", failed)
    assert view is not None and view.animated_src is not None
    assert view.illustration_note == (
        "The illustrated card could not be made, so the story card is used."
    )


def test_disabled_missing_and_old_manifests_have_no_note() -> None:
    for status in (None, IllustrationStatus.DISABLED):
        manifest = make_manifest(MediaOutcome.GIF, illustration_status=status)
        view = build_story_media_view("story-1", manifest)
        assert view is not None and view.illustration_note == ""


# --- 065b picker -------------------------------------------------------------------------------


def _illustration(**overrides: object) -> MediaArtifact:
    fields: dict[str, object] = {
        "artifact_id": "art-v1-" + "c" * 16,
        "content_hash": "c" * 64,
        "relative_path": f"{'c' * 64}.png",
        "alt_text": "A drawing of a rising line",
    }
    fields.update(overrides)
    return make_artifact(MediaKind.STATIC, **fields)


def _illustrated(
    status: IllustrationStatus = IllustrationStatus.MATCHED,
    outcome: MediaOutcome = MediaOutcome.GIF,
    **overrides: object,
) -> MediaManifest:
    return make_manifest(
        outcome,
        illustration_artifact=_illustration(),
        illustration_status=status,
        **overrides,
    )


def _page(
    manifest: MediaManifest | None,
    status: PrimaryStoryStatus = PrimaryStoryStatus.REVIEW,
) -> str:
    package = make_package(media=manifest)
    detail = make_detail(
        status=status,
        internal_status="review" if status is PrimaryStoryStatus.REVIEW else "ready",
        package=package,
        content_hash=story_package_hash(package),
        decided_by="dev@localhost" if status is PrimaryStoryStatus.READY else None,
        decided_at=NOW if status is PrimaryStoryStatus.READY else None,
    )
    response = client_for(FakeDetailService(detail)).get(f"/stories/{STORY_ID}")
    assert response.status_code == 200
    return response.text


def _tiles(text: str) -> list[str]:
    return re.findall(r"<label class=\"pick__tile[^>]*>.*?</label>", text, flags=re.S)


def test_build_pick_view_has_one_tile_per_candidate_and_none_below_two() -> None:
    manifest = _illustrated()
    view = build_story_pick_view("story-1", manifest)
    assert view is not None
    assert view.legend == PICK_LEGEND == "Picture for the post"
    assert view.form_id == "story-approve"
    assert [t.value for t in view.tiles] == ["illustration", "card"]
    assert [t.name for t in view.tiles] == ["Illustrated card", "Story card"]
    assert [t.checked for t in view.tiles] == [True, False]
    assert view.tiles[0].animated_src is None
    assert view.tiles[1].animated_src is not None
    assert view.tiles[0].hint == (
        "Headline with a drawing from our free illustration set, picked to match the story."
    )
    assert view.tiles[1].hint.startswith("Headline and key fact drawn by this app. ")
    assert build_story_pick_view("story-1", make_manifest(MediaOutcome.GIF)) is None
    assert build_story_pick_view("story-1", None) is None
    failed = make_manifest(MediaOutcome.STATIC, illustration_status=IllustrationStatus.FAILED)
    assert build_story_pick_view("story-1", failed) is None


def test_generic_pick_checks_the_card_and_uses_the_generic_hint() -> None:
    view = build_story_pick_view("story-1", _illustrated(IllustrationStatus.GENERIC))
    assert view is not None
    assert [t.checked for t in view.tiles] == [False, True]
    assert view.tiles[0].hint == (
        "Headline on a pattern made from this story. No drawing matched the topic."
    )


def test_review_renders_a_fieldset_picker_bound_to_the_approve_form() -> None:
    text = _page(_illustrated())
    assert '<fieldset class="pick__set">' in text
    assert '<legend class="pick__legend">Picture for the post</legend>' in text
    assert 'id="story-approve"' in text
    assert text.count('id="story-approve"') == 1
    tiles = _tiles(text)
    assert len(tiles) == 2
    for tile in tiles:
        assert 'type="radio" name="media_choice"' in tile
        assert 'form="story-approve"' in tile
    assert " checked" in tiles[0] and " checked" not in tiles[1]
    assert "<source media=" not in tiles[0]
    assert "<source media=" in tiles[1]
    assert "Alt text: A drawing of a rising line" in tiles[0]
    assert "story-media" not in text.split("pick__tiles")[0].split("story-detail__reading")[1]
    assert 'class="meta">Approve sends the picture you picked with the story.</p>' in text
    reject_form = text.split('<input type="hidden" name="decision" value="reject">')[0]
    assert reject_form.rsplit("<form", 1)[1].count("id=") == 0


def test_generic_review_checks_the_story_card() -> None:
    tiles = _tiles(_page(_illustrated(IllustrationStatus.GENERIC)))
    assert " checked" not in tiles[0] and " checked" in tiles[1]


def test_picker_renders_every_candidate_so_a_third_needs_no_template_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = MediaManifest.candidates

    def three(self: MediaManifest) -> tuple[MediaCandidate, ...]:
        found = real(self)
        photo = found[0].model_copy(update={"preselected": False})
        return (found[0], photo, found[1])

    monkeypatch.setattr(MediaManifest, "candidates", three)
    view = build_story_pick_view("story-1", _illustrated())
    assert view is not None and len(view.tiles) == 3
    assert len(_tiles(_page(_illustrated()))) == 3


def test_failed_review_shows_the_figure_and_note_with_no_picker() -> None:
    text = _page(make_manifest(MediaOutcome.GIF, illustration_status=IllustrationStatus.FAILED))
    assert 'class="story-media"' in text
    assert "The illustrated card could not be made, so the story card is used." in text
    assert "pick__set" not in text and 'name="media_choice"' not in text
    assert "Approve sends the picture you picked" not in text


def test_disabled_and_old_stories_render_the_single_figure_with_no_picker() -> None:
    for manifest in (
        make_manifest(MediaOutcome.GIF),
        make_manifest(MediaOutcome.GIF, illustration_status=IllustrationStatus.DISABLED),
    ):
        text = _page(manifest)
        assert 'class="story-media"' in text
        assert "pick__set" not in text and "Approve sends the picture" not in text


def test_ready_with_illustration_shows_only_that_picture() -> None:
    manifest = _illustrated(selected=MediaSelection.ILLUSTRATION)
    view = build_story_media_view("story-1", manifest)
    assert view is not None
    assert view.poster_src.endswith("art-v1-" + "c" * 16)
    assert view.animated_src is None and view.motion_note == ""
    assert view.alt_text == "A drawing of a rising line"
    assert view.kind_text == "Illustrated card. This picture goes in the post."
    text = _page(manifest, PrimaryStoryStatus.READY)
    assert "<source media=" not in text
    assert "Illustrated card. This picture goes in the post." in text
    assert "pick__set" not in text


def test_ready_with_card_adds_the_goes_in_the_post_text_and_none_is_unchanged() -> None:
    card = build_story_media_view("story-1", _illustrated(selected=MediaSelection.CARD))
    assert card is not None
    assert card.kind_text == "Animated card. Plays once, 4 seconds. This picture goes in the post."
    assert card.animated_src is not None
    plain = build_story_media_view("story-1", _illustrated())
    assert plain is not None
    assert plain.kind_text == "Animated card. Plays once, 4 seconds."
    assert "pick__set" not in _page(_illustrated(), PrimaryStoryStatus.READY)


def test_rail_note_only_with_the_picker() -> None:
    assert PICK_RAIL_NOTE not in _page(make_manifest(MediaOutcome.GIF))
    assert PICK_RAIL_NOTE in _page(_illustrated())


def test_package_note_matches_what_the_human_approves() -> None:
    card_only = _page(make_manifest(MediaOutcome.GIF))
    assert "You approve the text and the story card together." in card_only
    assert "the picture you pick" not in card_only
    picker = _page(_illustrated())
    assert "You approve the text and the picture you pick together." in picker
    assert "the story card together" not in picker
    picked = build_story_detail_view(
        make_detail(
            status=PrimaryStoryStatus.READY,
            internal_status="ready",
            package=make_package(media=_illustrated(selected=MediaSelection.ILLUSTRATION)),
        ),
        now=NOW,
    )
    assert picked.package_note == "You approved the text and the illustrated card together."
    ready = _page(_illustrated(selected=MediaSelection.ILLUSTRATION), PrimaryStoryStatus.READY)
    assert "Illustrated card. This picture goes in the post." in ready
    assert "story card together" not in ready


# --- 070 drawn gallery -------------------------------------------------------------------------

DRAWN_ROLES = ("hero", "concept", "detail", "setting")


def _drawn(roles: tuple[str, ...] = DRAWN_ROLES[:3], **overrides: object) -> MediaManifest:
    from ai_trend_pipeline.media.models import DrawnArtifact, DrawnRole, DrawnStatus

    images = tuple(
        DrawnArtifact(
            role=DrawnRole(role),
            artifact=make_artifact(
                MediaKind.STATIC,
                artifact_id="art-v1-" + f"{index + 1:x}" * 16,
                content_hash=f"{index + 1:x}" * 64,
                relative_path=f"{f'{index + 1:x}' * 64}.png",
                alt_text=f"AI drawing ({role}) of a rising line",
            ),
        )
        for index, role in enumerate(roles)
    )
    fields: dict[str, object] = {"drawn_artifacts": images, "drawn_status": DrawnStatus.DRAWN}
    fields.update(overrides)
    return make_manifest(MediaOutcome.GIF, **fields)


def _tray(text: str) -> str:
    match = re.search(r'<section class="drawn".*?</section>', text, flags=re.S)
    assert match is not None
    return match.group(0)


def test_build_drawn_gallery_view_has_tiles_in_role_order() -> None:
    from ai_trend_pipeline.web.view_models import (
        DRAWN_HEADING,
        DRAWN_NOTE,
        build_drawn_gallery_view,
    )

    view = build_drawn_gallery_view("story 1", _drawn(("detail", "hero", "concept")))
    assert view is not None
    assert view.heading == DRAWN_HEADING and view.note == DRAWN_NOTE
    assert view.alt_label == "Alt text:"
    assert [t.role for t in view.tiles] == ["hero", "concept", "detail"]
    assert [t.role_label for t in view.tiles] == [
        "Main scene",
        "The idea as a picture",
        "Supporting detail",
    ]
    assert view.tiles[0].src == "/stories/story%201/media/art-v1-" + "2" * 16
    assert view.tiles[0].alt_text == "AI drawing (hero) of a rising line"
    assert view.tiles[0].width == 1200 and view.tiles[0].height == 675
    assert build_drawn_gallery_view("s", None) is None
    assert build_drawn_gallery_view("s", make_manifest(MediaOutcome.GIF)) is None


def test_drawn_note_for_each_status() -> None:
    from ai_trend_pipeline.media.models import DrawnStatus
    from ai_trend_pipeline.web.view_models import (
        DRAWN_CAPPED_NOTE,
        DRAWN_FAILED_NOTE,
        drawn_note_for,
    )

    assert drawn_note_for(make_manifest(drawn_status=DrawnStatus.FAILED)) == DRAWN_FAILED_NOTE
    assert drawn_note_for(make_manifest(drawn_status=DrawnStatus.CAPPED)) == DRAWN_CAPPED_NOTE
    assert drawn_note_for(_drawn()) == ""
    assert drawn_note_for(make_manifest()) == ""
    assert drawn_note_for(None) == ""


def test_review_renders_one_tile_per_drawing_in_role_order() -> None:
    tray = _tray(_page(_drawn()))
    tiles = re.findall(r'<li class="drawn__tile[^"]*">.*?</li>', tray, flags=re.S)
    assert len(tiles) == 3
    assert "drawn__tile--hero" in tiles[0] and "drawn__tile--detail" in tiles[2]
    assert "Main scene" in tiles[0] and "The idea as a picture" in tiles[1]
    assert 'width="1200" height="675"' in tiles[0]
    assert 'alt="AI drawing (hero) of a rising line"' in tiles[0]
    assert "Alt text: AI drawing (hero) of a rising line" in tiles[0]
    assert "/media/art-v1-" + "1" * 16 in tiles[0]
    assert "Drawings for this story" in tray
    assert "Drawn by an AI model. For review only: these drawings are not posted." in tray


def test_four_drawings_render_four_tiles() -> None:
    tray = _tray(_page(_drawn(DRAWN_ROLES)))
    assert tray.count('<li class="drawn__tile') == 4
    assert "Setting" in tray


def test_tray_has_no_form_controls() -> None:
    tray = _tray(_page(_drawn()))
    for forbidden in ("<input", "<form", "form=", "<button", "<label"):
        assert forbidden not in tray


def test_failed_and_capped_show_only_the_note() -> None:
    from ai_trend_pipeline.media.models import DrawnStatus
    from ai_trend_pipeline.web.view_models import DRAWN_CAPPED_NOTE, DRAWN_FAILED_NOTE

    for status, note in (
        (DrawnStatus.FAILED, DRAWN_FAILED_NOTE),
        (DrawnStatus.CAPPED, DRAWN_CAPPED_NOTE),
    ):
        text = _page(make_manifest(MediaOutcome.GIF, drawn_status=status))
        assert f'<p class="meta drawn-missing">{note}</p>' in html.unescape(text)
        assert 'class="drawn"' not in text and "drawn__tile" not in text


def test_disabled_none_and_old_manifests_render_identically() -> None:
    from ai_trend_pipeline.web.view_models import StoryDetailView

    baseline = _page(make_manifest(MediaOutcome.GIF))
    assert "drawn__" not in baseline and 'class="drawn' not in baseline
    assert _page(make_manifest(MediaOutcome.GIF, drawn_status=None)) == baseline
    assert StoryDetailView().drawn is None and StoryDetailView().drawn_note == ""


def test_ready_with_drawings_shows_no_tray() -> None:
    text = _page(_drawn(), PrimaryStoryStatus.READY)
    assert "drawn__" not in text and 'class="drawn' not in text


def test_drawings_do_not_change_the_picker_or_package_note() -> None:
    plain = _page(_illustrated())
    drawn = _drawn()
    with_drawings = _page(
        _illustrated(drawn_artifacts=drawn.drawn_artifacts, drawn_status=drawn.drawn_status)
    )
    assert len(_tiles(with_drawings)) == len(_tiles(plain)) == 2
    assert re.findall(r"<input[^>]*checked[^>]*>", with_drawings) == re.findall(
        r"<input[^>]*checked[^>]*>", plain
    )
    assert 'class="drawn"' in with_drawings
    assert 'name="media_choice"' in with_drawings
