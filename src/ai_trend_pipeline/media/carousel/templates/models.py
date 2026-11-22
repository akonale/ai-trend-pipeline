"""Slide template models. They never cross a Temporal boundary."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_trend_pipeline.media.models import MAX_ALT_TEXT_CHARS, clean_media_text

MAX_SLIDE_TEXT_CHARS = 200
MAX_SLIDE_CAPTION_CHARS = 140
MAX_IMAGE_DESCRIPTION_CHARS = 160


class SlideTemplateName(StrEnum):
    HOOK = "hook"
    POINT = "point"
    NUMBER = "number"
    SOURCE = "source"


class SlideOverlay(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_SLIDE_TEXT_CHARS)
    caption: str | None = Field(default=None, max_length=MAX_SLIDE_CAPTION_CHARS)
    position: int = Field(ge=1, le=4)
    total: int = Field(ge=2, le=4)
    image_description: str | None = Field(default=None, max_length=MAX_IMAGE_DESCRIPTION_CHARS)

    @field_validator("text", "caption", "image_description", mode="before")
    @classmethod
    def _clean(cls, value: object) -> object:
        return clean_media_text(value)

    @model_validator(mode="after")
    def _position_within_total(self) -> Self:
        if self.position > self.total:
            raise ValueError("position must not exceed total")
        return self


class ComposedSlide(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    template: SlideTemplateName
    png: bytes
    width: int
    height: int
    alt_text: str = Field(min_length=1, max_length=MAX_ALT_TEXT_CHARS)
    truncated: bool
