"""`IllustratedCardProvider`: match a topic, draw the scene, encode a PNG. Offline and pure."""

from dataclasses import dataclass
from pathlib import Path

from ai_trend_pipeline.media.illustration import IllustrationOutput
from ai_trend_pipeline.media.illustrations.catalogue import (
    ASSET_DIR,
    CATALOGUE_PATH,
    IllustrationAsset,
    catalogue_sha256,
    load_catalogue,
    read_asset_svg,
)
from ai_trend_pipeline.media.illustrations.matching import match_story
from ai_trend_pipeline.media.illustrations.sanitize import UnsafeSvg, sanitize_svg
from ai_trend_pipeline.media.illustrations.scene import (
    SCENE_SIZE,
    SCENE_TEMPLATE_VERSION,
    draw_scene,
    encode_png,
    layout_scene,
    recolour_svg,
    scene_spec,
    svg_aspect,
)
from ai_trend_pipeline.media.models import IllustrateInput, IllustrationStatus, MediaType
from ai_trend_pipeline.media.providers import MediaContentUnsupported, RenderedMedia

PROVIDER_NAME = "illustrated-card"


@dataclass(frozen=True)
class _LoadedAsset:
    asset: IllustrationAsset
    svg: str


class IllustratedCardProvider:
    name = PROVIDER_NAME

    def __init__(self, catalogue_path: Path = CATALOGUE_PATH, asset_dir: Path = ASSET_DIR) -> None:
        self._catalogue_path = catalogue_path
        self._asset_dir = asset_dir
        self._loaded: tuple[_LoadedAsset, ...] | None = None
        self.version = f"{SCENE_TEMPLATE_VERSION}-{catalogue_sha256(catalogue_path)[:16]}"

    def _assets(self) -> tuple[_LoadedAsset, ...]:
        """Load the catalogue once. Every file must match its SHA-256 and pass the sanitiser."""
        if self._loaded is None:
            try:
                loaded = tuple(
                    _LoadedAsset(
                        asset,
                        sanitize_svg(read_asset_svg(asset, self._asset_dir), asset.recolour.keys()),
                    )
                    for asset in load_catalogue(self._catalogue_path)
                )
            except (OSError, ValueError, UnsafeSvg) as exc:
                raise MediaContentUnsupported(f"illustration catalogue is broken: {exc}") from exc
            self._loaded = loaded
        return self._loaded

    def illustrate(self, data: IllustrateInput) -> IllustrationOutput:
        loaded = self._assets()
        by_id = {item.asset.id: item for item in loaded}
        spec = scene_spec(data.story_content_hash)
        match = match_story(
            data.headline,
            data.summary,
            [item.asset for item in loaded],
            spec.asset_pick,
        )
        if match.asset is None:
            layout = layout_scene(data.headline, spec, aspect=None)
            image = draw_scene(layout, None)
            status, description = IllustrationStatus.GENERIC, None
        else:
            item = by_id[match.asset.id]
            svg = recolour_svg(item.svg, dict(item.asset.recolour), spec)
            layout = layout_scene(data.headline, spec, aspect=svg_aspect(svg))
            image = draw_scene(layout, svg)
            status, description = IllustrationStatus.MATCHED, item.asset.description
        width, height = SCENE_SIZE
        rendered = RenderedMedia(encode_png(image), MediaType.PNG, width, height, None)
        return IllustrationOutput(status=status, rendered=rendered, description=description)
