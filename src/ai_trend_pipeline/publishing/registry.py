"""Destination capability registry. Pure data."""

from collections.abc import Mapping

from ai_trend_pipeline.media.models import MediaType
from ai_trend_pipeline.publishing.models import DestinationCapabilities, DestinationName

BLUESKY_CAPABILITIES = DestinationCapabilities(
    destination=DestinationName.BLUESKY,
    max_text_chars=300,
    max_text_bytes=3000,
    max_images=4,
    static_media_types=(MediaType.PNG, MediaType.WEBP),
    max_static_bytes=1_000_000,
    supports_animation=False,
    animated_media_types=(),
    max_animated_bytes=0,
    max_alt_text_chars=2000,
    supports_link_facets=True,
)

DESTINATION_CAPABILITIES: Mapping[DestinationName, DestinationCapabilities] = {
    DestinationName.BLUESKY: BLUESKY_CAPABILITIES,
}


def capabilities_for(name: DestinationName) -> DestinationCapabilities:
    return DESTINATION_CAPABILITIES[name]
