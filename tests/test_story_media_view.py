"""Reduced-motion media view: poster for everyone, GIF only when motion is fine."""

from ai_trend_pipeline.media.models import MediaOutcome
from ai_trend_pipeline.web.view_models import build_story_media_view
from tests.media_fixtures import make_manifest


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
