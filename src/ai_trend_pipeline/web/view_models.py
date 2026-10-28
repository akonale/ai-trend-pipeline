"""Pydantic models shaped for templates."""

from pydantic import BaseModel


class HealthView(BaseModel):
    status: str
    temporal_address: str
