from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import shutil
import sys

import pytest

from hacxgent.cli.entrypoint import main
from hacxgent.core.agent_loop import AgentLoop
from hacxgent.core.tools.base import BaseToolConfig, ToolPermission
from hacxgent.core.types import FunctionCall, ToolCall
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend

EXPECTED_BASELINE_PROMPT = "You are operating as and within Hacxgent"


def _todo_tool_call() -> ToolCall:
    return ToolCall(
        id="todo-call",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action": "read"}'),
    )


def _build_args(**kwargs):
    defaults = dict(
        initial_prompt=None,
        prompt=None,
        max_turns=None,
        max_price=None,
        enabled_tools=None,
        output="text",
        agent="default",
        setup=False,
        workdir=None,
        diagnose=True,
        trusted_local=False,
        continue_session=False,
        resume=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _prepare_temp_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    temp_home = tmp_path / "home"
    temp_hacxgent = temp_home / ".hacxgent"
    temp_hacxgent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path.home() / ".hacxgent" / "settings.json", temp_hacxgent / "settings.json")
    monkeypatch.setenv("HACXGENT_HOME", str(temp_hacxgent))


def test_cli_diagnose_and_todo_callback_uses_real_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("HACXGENT_AUTO_APPROVE", "false")
    monkeypatch.setenv("HACXGENT_TRUSTED_LOCAL", "false")
    monkeypatch.setenv("FREELLMAPI_API_KEY", "mock")
    _prepare_temp_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        AgentLoop,
        "_select_backend",
        lambda self: FakeBackend(
            [
                mock_llm_chunk(
                    content="Checking your todos.",
                    tool_calls=[_todo_tool_call()],
                ),
                mock_llm_chunk(content="Retrieved 0 todos"),
            ]
        ),
    )

    captured: dict[str, AgentLoop] = {}

    def fake_run_textual_ui(agent_loop: AgentLoop, initial_prompt: str | None = None):
        captured["agent_loop"] = agent_loop
        assert agent_loop.approval_callback is not None
        assert agent_loop.tool_manager.get("todo")
        agent_loop.config.tools["todo"] = BaseToolConfig(permission=ToolPermission.ASK)

        async def _run() -> list[object]:
            return [event async for event in agent_loop.act("What's my todo list?")]

        events = asyncio.run(_run())
        assert any(getattr(event, "tool_name", None) == "todo" for event in events)
        return "done"

    monkeypatch.setattr("hacxgent.cli.cli.run_textual_ui", fake_run_textual_ui)
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "y")
    monkeypatch.setattr(sys, "argv", ["hacxgent", "--diagnose"])

    main()

    output = capsys.readouterr().out
    assert '"system_prompt_id": "cli"' in output
    assert '"approval_callback_mode": "interactive"' in output
    assert '"todo"' in output
    assert '"tool_permissions": {' in output
    assert EXPECTED_BASELINE_PROMPT in output
    assert "Claw-Maximum-Autonomy" not in output

    agent_loop = captured["agent_loop"]
    assert agent_loop is not None
    assert agent_loop.approval_callback is not None
    assert agent_loop.config.tools["todo"].permission is ToolPermission.ASK


def test_cli_trusted_local_executes_todo_without_callback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("HACXGENT_AUTO_APPROVE", "false")
    monkeypatch.setenv("FREELLMAPI_API_KEY", "mock")
    _prepare_temp_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "hacxgent.cli.entrypoint.check_and_resolve_trusted_folder",
        lambda: pytest.fail("trusted-local should skip the trust prompt"),
    )
    monkeypatch.setattr(sys, "argv", ["hacxgent", "--diagnose", "--trusted-local"])
    monkeypatch.setattr(
        AgentLoop,
        "_select_backend",
        lambda self: FakeBackend(
            [
                mock_llm_chunk(
                    content="Checking your todos.",
                    tool_calls=[_todo_tool_call()],
                ),
                mock_llm_chunk(content="Retrieved 0 todos"),
            ]
        ),
    )

    captured: dict[str, AgentLoop] = {}

    def fake_run_textual_ui(agent_loop: AgentLoop, initial_prompt: str | None = None):
        captured["agent_loop"] = agent_loop
        assert agent_loop.approval_callback is None

        async def _run() -> list[object]:
            return [event async for event in agent_loop.act("What's my todo list?")]

        events = asyncio.run(_run())
        assert any(getattr(event, "tool_name", None) == "todo" for event in events)
        return "done"

    monkeypatch.setattr("hacxgent.cli.cli.run_textual_ui", fake_run_textual_ui)

    main()

    output = capsys.readouterr().out
    assert '"trusted_local_execution": true' in output.lower()
    assert '"approval_callback_mode": "trusted-local"' in output

    agent_loop = captured["agent_loop"]
    assert agent_loop is not None
    assert agent_loop.approval_callback is None
    assert agent_loop.auto_approve is True
    assert agent_loop.tool_manager.available_tools
