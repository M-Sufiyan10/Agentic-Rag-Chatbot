"""Request/response models for the feedback endpoints."""

from typing import Literal

from pydantic import BaseModel


class FeedbackIn(BaseModel):
    value: Literal["up", "down"]


class FeedbackOut(BaseModel):
    value: Literal["up", "down"]
