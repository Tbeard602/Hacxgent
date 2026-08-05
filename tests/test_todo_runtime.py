from __future__ import annotations

import json
from pathlib import Path
import shutil

from pydantic import BaseModel
import pytest

from hacxgent.cli.textual_ui.app import HacxgentApp
from hacxgent.core.agent_loop import AgentLoop
from hacxgent.core.config import HacxgentConfig
from hacxgent.core.tools.base import ToolPermission
from hacxgent.core.types import ApprovalResponse, FunctionCall, ToolCall
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend


def _load_real_local_config() -> HacxgentConfig:
    raise RuntimeError("internal helper should not be called directly")


def _load_temp_local_config(temp_home: Path) -> HacxgentConfig:
    config_path = temp_home / ".hacxgent" / "settings.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["include_project_context"] = False
    data["include_prompt_detail"] = False
    data["include_model_info"] = False
    data["include_commit_signature"] = False
    data["active_model"] = "AUTO"
    data["auto_approve"] = False
    data["trusted_local_execution"] = False
    return HacxgentConfig.model_validate(data)


def _prepare_temp_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    temp_home = tmp_path / "home"
    temp_hacxgent = temp_home / ".hacxgent"
    temp_hacxgent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        Path.home() / ".hacxgent" / "settings.json", temp_hacxgent / "settings.json"
    )
    monkeypatch.setenv("HACXGENT_HOME", str(temp_hacxgent))


def _make_todo_tool_call(call_id: str = "todo-call") -> ToolCall:
    return ToolCall(
        id=call_id,
        index=0,
        function=FunctionCall(name="todo", arguments='{"action": "read"}'),
    )


@pytest.mark.asyncio
async def test_real_local_config_todo_call_approves_through_ui_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FREELLMAPI_API_KEY", "mock")
    _prepare_temp_home(monkeypatch, tmp_path)
    config = _load_temp_local_config(tmp_path / "home")
    backend = FakeBackend(
        [
            mock_llm_chunk(
                content="Checking your todos.",
                tool_calls=[_make_todo_tool_call()],
            ),
            mock_llm_chunk(content="Retrieved 0 todos"),
        ]
    )
    agent = AgentLoop(config=config, backend=backend)
    app = HacxgentApp(agent_loop=agent)

    def approval_callback(
        _tool_name: str, _args: BaseModel, _tool_call_id: str
    ) -> tuple[ApprovalResponse, str | None]:
        return (ApprovalResponse.YES, None)

    agent.set_approval_callback(approval_callback)
    events = [event async for event in agent.act("What's my todo list?")]

    assert any(getattr(event, "tool_name", None) == "todo" for event in events)
    assert backend.requests_messages
    assert app.agent_loop.tool_manager.get("todo").config.permission is ToolPermission.ALWAYS


@pytest.mark.asyncio
async def test_real_local_config_todo_call_executes_in_trusted_local_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FREELLMAPI_API_KEY", "mock")
    _prepare_temp_home(monkeypatch, tmp_path)
    config = _load_temp_local_config(tmp_path / "home")
    config.trusted_local_execution = True
    backend = FakeBackend(
        [
            mock_llm_chunk(
                content="Checking your todos.",
                tool_calls=[_make_todo_tool_call()],
            ),
            mock_llm_chunk(content="Retrieved 0 todos"),
        ]
    )
    agent = AgentLoop(config=config, backend=backend)

    events = [event async for event in agent.act("What's my todo list?")]

    assert any(getattr(event, "tool_name", None) == "todo" for event in events)
    assert agent.approval_callback is None
    assert agent.auto_approve is True
