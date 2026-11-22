"""Provider-neutral contracts between the drawn-images activity and an image backend."""

from pydantic import BaseModel, ConfigDict, Field

from ai_trend_pipeline.media.models import MAX_DRAWN_IMAGES, DrawnRole


class ImageBrief(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    headline: str
    summary: str
    roles: tuple[DrawnRole, ...] = Field(min_length=1, max_length=MAX_DRAWN_IMAGES)


class GeneratedImage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: DrawnRole
    png: bytes
    width: int
    height: int


class ImageBatch(BaseModel):
    """An empty `images` means nothing usable was produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    images: tuple[GeneratedImage, ...] = ()
    rejects: dict[str, int] = Field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
