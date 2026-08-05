from __future__ import annotations

from collections.abc import AsyncGenerator
import json
import os
from typing import Any

import httpx

from hacxgent.core.cache import (
    CacheManager,
    canonical_cache_key,
    contains_unmasked_sensitive,
)
from hacxgent.core.config import Backend, ModelConfig, ProviderConfig
from hacxgent.core.llm.exceptions import BackendErrorBuilder
from hacxgent.core.llm.types import BackendLike
from hacxgent.core.types import (
    AvailableTool,
    FunctionCall,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    Role,
    StrToolChoice,
    ToolCall,
)
from hacxgent.core.utils import get_user_agent


class OpenAIAdapter:
    def _reasoning_to_api(
        self, msg_dict: dict[str, Any], field_name: str
    ) -> dict[str, Any]:
        result = dict(msg_dict)
        reasoning = result.pop("reasoning_content", None)
        if reasoning is not None:
            result[field_name] = reasoning
        return result

    def _reasoning_from_api(
        self, msg_dict: dict[str, Any], field_name: str
    ) -> dict[str, Any]:
        result = dict(msg_dict)
        if field_name in result:
            result["reasoning_content"] = result.pop(field_name)
        return result


class GenericBackend(BackendLike):
    def __init__(self, provider: ProviderConfig, timeout: float = 720.0, *, cache: CacheManager | None = None, cache_llm_responses: bool = False, cache_context: dict[str, Any] | None = None) -> None:
        self._provider = provider
        self._timeout = timeout
        self._client: Any | None = None
        self._cache = cache
        self._cache_llm_responses = cache_llm_responses
        self._cache_context = cache_context or {}

    async def __aenter__(self) -> GenericBackend:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def _endpoint(self) -> str:
        return f"{self._provider.api_base.rstrip('/')}/chat/completions"

    def _reasoning_field_name(self) -> str:
        field_name = getattr(self._provider, "reasoning_field_name", "") or "reasoning_content"
        return field_name

    def _headers(self, extra_headers: dict[str, str] | None) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "user-agent": get_user_agent(self._provider.backend),
        }
        token = (
            os.getenv(self._provider.api_key_env_var)
            if self._provider.api_key_env_var
            else ""
        )
        if token:
            # Use a standard Authorization header for outgoing requests. Masking is handled separately for logs.
            headers["Authorization"] = f"Bearer {token}"
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _mask_headers(self, headers: dict[str, str] | None) -> dict[str, str] | None:
        """Return a copy of headers with sensitive values masked for logging/error reporting."""
        if not headers:
            return None
        masked: dict[str, str] = {}
        for k, v in dict(headers).items():
            try:
                val = str(v)
            except Exception:
                val = ""
            if not val:
                masked[k] = val
                continue
            if len(val) <= 6:
                masked[k] = "******"
            else:
                masked[k] = val[:3] + "******"
        return masked


    def _tool_choice_payload(
        self, tool_choice: StrToolChoice | AvailableTool | None
    ) -> Any:
        if tool_choice is None or isinstance(tool_choice, str):
            return tool_choice
        return tool_choice.model_dump(exclude_none=True)

    def _prepare_tools(
        self, tools: list[AvailableTool] | None
    ) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [tool.model_dump(exclude_none=True) for tool in tools]

    def _prepare_messages(self, messages: list[LLMMessage]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for msg in messages:
            item: dict[str, Any] = {
                "role": msg.role.value if isinstance(msg.role, Role) else str(msg.role),
                "content": msg.content or "",
            }
            if msg.reasoning_content is not None:
                item["reasoning_content"] = msg.reasoning_content
            if msg.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.id,
                        "index": tc.index,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            if msg.role == Role.tool:
                item["tool_call_id"] = msg.tool_call_id
                item["name"] = msg.name
            payload.append(item)
        return payload

    def _cache_key(self, model: ModelConfig, messages: list[LLMMessage], temperature: float, tools: list[AvailableTool] | None, tool_choice: StrToolChoice | AvailableTool | None, max_tokens: int | None, response_format: Any = None) -> str:
        return canonical_cache_key({
            "provider": self._provider.name,
            "model": model.name,
            "system_prompt": next((m["content"] for m in self._prepare_messages(messages) if m["role"] == "system"), ""),
            "messages": self._prepare_messages(messages),
            "tools": self._prepare_tools(tools),
            "tool_choice": self._tool_choice_payload(tool_choice),
            "temperature": temperature,
            "sampling": {"max_tokens": max_tokens},
            "response_format": response_format,
            **self._cache_context,
        })

    def _cacheable_chunk(self, chunk: LLMChunk) -> bool:
        return not chunk.message.tool_calls and not contains_unmasked_sensitive(chunk.model_dump())

    def _build_chunk(self, payload: dict[str, Any]) -> LLMChunk:
        choices = payload.get("choices") or []
        if not choices:
            return LLMChunk(message=LLMMessage(role=Role.assistant, content=""))

        choice = choices[0]
        data = choice.get("message") or choice.get("delta") or {}
        tool_calls_data = data.get("tool_calls") or []
        tool_calls = None
        if tool_calls_data:
            tool_calls = [
                ToolCall(
                    id=tc.get("id"),
                    index=tc.get("index"),
                    type=tc.get("type", "function"),
                    function=FunctionCall(
                        name=(tc.get("function") or {}).get("name"),
                        arguments=(tc.get("function") or {}).get("arguments"),
                    ),
                )
                for tc in tool_calls_data
            ]

        message = LLMMessage(
            role=Role(data.get("role", "assistant")),
            content=data.get("content", ""),
            reasoning_content=data.get(self._reasoning_field_name())
            or data.get("reasoning_content")
            or data.get("reasoning")
            or None,
            tool_calls=tool_calls,
        )
        usage_data = payload.get("usage") or {}
        usage = LLMUsage(prompt_tokens=0, completion_tokens=0)
        if usage_data:
            usage = LLMUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
            )
        return LLMChunk(message=message, usage=usage)

    async def complete(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        extra_headers: dict[str, str] | None,
        response_format: Any = None,
    ) -> LLMChunk:
        async def compute() -> LLMChunk:
            client = self._client_or_create()
            payload: dict[str, Any] = {
            "model": model.name,
            "messages": self._prepare_messages(messages),
            "temperature": temperature,
            "stream": False,
            }
            if tools:
                payload["tools"] = self._prepare_tools(tools)
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            if tool_choice is not None:
                payload["tool_choice"] = self._tool_choice_payload(tool_choice)
            if response_format is not None:
                payload["response_format"] = response_format

            try:
                response = await client.post(
                self._endpoint(),
                json=payload,
                headers=self._headers(extra_headers),
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise BackendErrorBuilder.build_http_error(
                provider=self._provider.name,
                endpoint=self._endpoint(),
                response=exc.response,
                headers=exc.request.headers if exc.request else None,
                model=model.name,
                messages=messages,
                temperature=temperature,
                has_tools=bool(tools),
                tool_choice=tool_choice,
                ) from exc
            except httpx.RequestError as exc:
                raise BackendErrorBuilder.build_request_error(
                provider=self._provider.name,
                endpoint=self._endpoint(),
                error=exc,
                model=model.name,
                messages=messages,
                temperature=temperature,
                has_tools=bool(tools),
                tool_choice=tool_choice,
                ) from exc
            return self._build_chunk(response.json())

        if not self._cache or not self._cache_llm_responses or tools or tool_choice not in {None, "none"}:
            return await compute()
        key = self._cache_key(model, messages, temperature, tools, tool_choice, max_tokens, response_format)
        result = await self._cache.get_or_compute("llm_text", key, compute, ttl=self._cache.ttls.get("llm_text", 3600), metadata={"provider": self._provider.name, "model": model.name}, force_refresh=os.getenv("HACXGENT_REFRESH_CACHE") == "1", cache_result=lambda item: self._cacheable_chunk(item))
        return LLMChunk.model_validate(result) if isinstance(result, dict) else result

    async def complete_streaming(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        extra_headers: dict[str, str] | None,
        response_format: Any = None,
    ) -> AsyncGenerator[LLMChunk, None]:
        stream_completed = False

        async def collect() -> list[dict[str, Any]]:
            nonlocal stream_completed
            client = self._client_or_create()
            chunks: list[LLMChunk] = []
            payload: dict[str, Any] = {
                "model": model.name,
                "messages": self._prepare_messages(messages),
                "temperature": temperature,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if self._provider.backend == Backend.HACXGENT or self._provider.name == "hacxgent" or getattr(self, "_stream_tool_calls", False):
                payload["stream_options"]["stream_tool_calls"] = True
            if tools:
                payload["tools"] = self._prepare_tools(tools)
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            if tool_choice is not None:
                payload["tool_choice"] = self._tool_choice_payload(tool_choice)
            if response_format is not None:
                payload["response_format"] = response_format
            try:
                async with client.stream(
                    "POST", self._endpoint(), json=payload, headers=self._headers(extra_headers)
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            line = line.removeprefix("data: ")
                        if line == "[DONE]":
                            stream_completed = True
                            break
                        chunks.append(self._build_chunk(json.loads(line)))
            except httpx.HTTPStatusError as exc:
                raise BackendErrorBuilder.build_http_error(
                    provider=self._provider.name, endpoint=self._endpoint(), response=exc.response,
                    headers=exc.request.headers if exc.request else None, model=model.name,
                    messages=messages, temperature=temperature, has_tools=bool(tools), tool_choice=tool_choice,
                ) from exc
            except httpx.RequestError as exc:
                raise BackendErrorBuilder.build_request_error(
                    provider=self._provider.name, endpoint=self._endpoint(), error=exc, model=model.name,
                    messages=messages, temperature=temperature, has_tools=bool(tools), tool_choice=tool_choice,
                ) from exc
            return [chunk.model_dump(mode="json") for chunk in chunks]

        chunks_data: list[dict[str, Any]]
        cache_allowed = bool(self._cache and self._cache_llm_responses and not tools and tool_choice in {None, "none"})
        if cache_allowed:
            cache = self._cache
            assert cache is not None
            key = self._cache_key(model, messages, temperature, tools, tool_choice, max_tokens, response_format)
            chunks_data = await cache.get_or_compute("llm_text", key, collect, ttl=cache.ttls.get("llm_text", 3600), metadata={"provider": self._provider.name, "model": model.name}, force_refresh=os.getenv("HACXGENT_REFRESH_CACHE") == "1", cache_result=lambda items: stream_completed and not any(LLMChunk.model_validate(item).message.tool_calls for item in items) and not contains_unmasked_sensitive(items))
        else:
            chunks_data = await collect()
        chunks = [LLMChunk.model_validate(item) for item in chunks_data]
        aggregate = LLMChunk(message=LLMMessage(role=Role.assistant))
        for chunk in chunks:
            aggregate += chunk
        if not self._cacheable_chunk(aggregate) and cache_allowed:
            cache = self._cache
            assert cache is not None
            cache.delete("llm_text", self._cache_key(model, messages, temperature, tools, tool_choice, max_tokens, response_format))
        for chunk in chunks:
            yield chunk

    async def count_tokens(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        tools: list[AvailableTool] | None,
        tool_choice: StrToolChoice | AvailableTool | None = None,
        extra_headers: dict[str, str] | None,
    ) -> int:
        return sum(len(message.content or "") for message in messages)

    async def vision(
        self,
        *,
        model: ModelConfig,
        prompt: str,
        image: str | bytes | list[str],
        detail: str = "auto",
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        return ""
