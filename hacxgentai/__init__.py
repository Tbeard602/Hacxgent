from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(slots=True)
class TextChunk:
    type: Literal["text"]
    text: str


@dataclass(slots=True)
class ThinkChunk:
    type: Literal["thinking"]
    thinking: list[TextChunk] = field(default_factory=list)


@dataclass(slots=True)
class AssistantMessage:
    content: str | list[ContentChunk]


ContentChunk = TextChunk | ThinkChunk
