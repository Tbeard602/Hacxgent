from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hacxgent.core.config import ProviderConfig
from hacxgent.core.llm.backend.generic import GenericBackend


@dataclass(slots=True, eq=True)
class ParsedContent:
    content: str
    reasoning_content: str | None


class HacxgentMapper:
    def parse_content(self, content: Any) -> ParsedContent:
        if isinstance(content, str):
            return ParsedContent(content=content, reasoning_content=None)

        reasoning_parts: list[str] = []
        text_parts: list[str] = []
        for chunk in content or []:
            chunk_type = getattr(chunk, "type", None)
            if chunk_type == "thinking":
                for inner in getattr(chunk, "thinking", []) or []:
                    reasoning_parts.append(getattr(inner, "text", ""))
            elif chunk_type == "text":
                text_parts.append(getattr(chunk, "text", ""))

        return ParsedContent(
            content="".join(text_parts),
            reasoning_content="".join(reasoning_parts) or None,
        )

    def prepare_message(self, msg: Any) -> Any:
        import hacxgentai

        content = msg.content or ""
        reasoning = msg.reasoning_content
        if reasoning is None:
            return hacxgentai.AssistantMessage(content=content)
        return hacxgentai.AssistantMessage(
            content=[
                hacxgentai.ThinkChunk(
                    type="thinking",
                    thinking=[hacxgentai.TextChunk(type="text", text=reasoning)],
                ),
                hacxgentai.TextChunk(type="text", text=content),
            ]
        )


class HacxgentBackend(GenericBackend):
    def __init__(self, provider: ProviderConfig, timeout: float = 720.0, **kwargs: Any) -> None:
        if getattr(provider, "reasoning_field_name", "reasoning_content") not in {
            "",
            "reasoning_content",
        }:
            raise ValueError(
                "Hacxgent backend does not support custom reasoning_field_name"
            )
        super().__init__(provider=provider, timeout=timeout, **kwargs)
        self._stream_tool_calls = True
