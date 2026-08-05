from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from acp.schema import AllowedOutcome, RequestPermissionResponse
import pytest

from hacxgent.acp import acp_agent_loop as acp_module
from hacxgent.acp.acp_agent_loop import HacxgentAcpAgentLoop
from hacxgent.acp.utils import ToolOption
from hacxgent.core.agent_loop import AgentLoop
from hacxgent.core.types import FunctionCall, ToolCall
from tests.acp.conftest import _create_acp_agent
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend


def _todo_tool_call() -> ToolCall:
    return ToolCall(
        id="todo-call",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action":"read"}'),
    )


class PatchedAgentLoop(AgentLoop):
    def __init__(self, *args, backend: FakeBackend, **kwargs) -> None:
        super().__init__(*args, **kwargs, backend=backend)


def _make_agent(backend: FakeBackend) -> HacxgentAcpAgentLoop:
    patch(
        "hacxgent.acp.acp_agent_loop.AgentLoop",
        side_effect=lambda *args, **kwargs: PatchedAgentLoop(
            *args, backend=backend, **kwargs
        ),
    ).start()
    return _create_acp_agent()


@pytest.mark.asyncio
async def test_acp_new_session_todo_uses_permission_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeBackend(
        [
            mock_llm_chunk(content="Checking your todos.", tool_calls=[_todo_tool_call()]),
            mock_llm_chunk(content="Retrieved 0 todos"),
        ]
    )
    agent = _make_agent(backend)

    async def request_permission(*_args, **_kwargs):
        return RequestPermissionResponse(
            outcome=AllowedOutcome(
                outcome="selected", option_id=ToolOption.ALLOW_ONCE
            )
        )

    monkeypatch.setattr(agent.client, "request_permission", request_permission)

    session_response = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    session = next(s for s in agent.sessions.values() if s.id == session_response.session_id)

    events = [event async for event in session.agent_loop.act("What's my todo list?")]

    assert any(getattr(event, "tool_name", None) == "todo" for event in events)
    assert backend.requests_messages
    assert session.agent_loop.approval_callback is not None


@pytest.mark.asyncio
async def test_acp_trusted_local_todo_skips_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeBackend(
        [
            mock_llm_chunk(content="Checking your todos.", tool_calls=[_todo_tool_call()]),
            mock_llm_chunk(content="Retrieved 0 todos"),
        ]
    )
    agent = _make_agent(backend)
    monkeypatch.setattr(acp_module, "TRUSTED_LOCAL_MODE", True)

    session_response = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    session = next(s for s in agent.sessions.values() if s.id == session_response.session_id)

    events = [event async for event in session.agent_loop.act("What's my todo list?")]

    assert any(getattr(event, "tool_name", None) == "todo" for event in events)
    assert session.agent_loop.approval_callback is None
    assert session.agent_loop.config.trusted_local_execution is True
    assert session.agent_loop.tool_manager.available_tools
