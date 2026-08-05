from __future__ import annotations

import inspect

from textual.app import App
from textual.widgets import Static

from hacxgent.cli.textual_ui.ansi_markdown import AnsiMarkdown, AnsiMarkdownFence
from tests.conftest import build_test_agent_loop, build_test_hacxgent_app
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from hacxgent.cli.textual_ui.widgets.messages import AssistantMessage
from hacxgent.cli.textual_ui.widgets.tool_widgets import (
    AskUserQuestionResultWidget,
    BashResultWidget,
)
from hacxgent.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from hacxgent.core.tools.builtins.ask_user_question import AskUserQuestionResult
from hacxgent.core.tools.builtins.bash import BashResult
from hacxgent.core.types import FunctionCall, LLMMessage, Role, ToolCall


class _WidgetApp(App):
    def __init__(self, widget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self):
        yield self._widget


async def _render_widget(widget) -> None:
    async with _WidgetApp(widget).run_test() as pilot:
        await pilot.pause(0.2)


def test_assistant_message_normalizes_none_content() -> None:
    message = AssistantMessage(None)
    assert message._content == ""


def test_ansi_markdown_fence_signature_accepts_textual_kwargs() -> None:
    sig = inspect.signature(AnsiMarkdownFence.highlight)

    assert "ansi" in sig.parameters
    assert "dark" in sig.parameters
    assert sig.parameters["ansi"].default is False
    assert sig.parameters["dark"].default is False


def test_ansi_markdown_fence_highlight_accepts_textual_keyword_arguments() -> None:
    result_plain = AnsiMarkdownFence.highlight('print("hello")', "python", ansi=False)
    result_ansi = AnsiMarkdownFence.highlight('print("hello")', "python", ansi=True)
    result_dark = AnsiMarkdownFence.highlight('print("hello")', "python", dark=True)
    result_empty_lexer = AnsiMarkdownFence.highlight('print("hello")', "", ansi=False)
    result_no_language = AnsiMarkdownFence.highlight('print("hello")', None, ansi=False)

    assert result_plain is not None
    assert result_ansi is not None
    assert result_dark is not None
    assert result_empty_lexer is not None
    assert result_no_language is not None


def test_ansi_markdown_renders_failing_markdown() -> None:
    markdown = AnsiMarkdown()

    async def run() -> None:
        async with _WidgetApp(markdown).run_test() as pilot:
            markdown.update("```python\nprint('hello')\n```\n\n```")
            await pilot.pause(0.1)

    import asyncio

    asyncio.run(run())
    assert markdown is not None


def test_installed_hacxgent_uses_this_checkout() -> None:
    import inspect

    from hacxgent.cli.entrypoint import main as cli_main

    assert "New OpenCode Project/Hacxgent" in inspect.getfile(cli_main)
    assert "New OpenCode Project/Hacxgent" in inspect.getfile(AnsiMarkdownFence)


def test_bash_result_widget_handles_none_stdout_and_stderr() -> None:
    result = BashResult.model_construct(
        command="ideviceinfo",
        stdout=None,
        stderr="ERROR: No device found!",
        returncode=255,
    )
    widget = BashResultWidget(result, success=False, message="failed", collapsed=False)

    async def run() -> None:
        await _render_widget(widget)

    import asyncio

    asyncio.run(run())
    assert widget.result is result


def test_bash_result_widget_handles_both_streams_none() -> None:
    result = BashResult.model_construct(
        command="ideviceinfo",
        stdout=None,
        stderr=None,
        returncode=255,
    )
    widget = BashResultWidget(result, success=False, message="failed", collapsed=True)

    async def run() -> None:
        await _render_widget(widget)

    import asyncio

    asyncio.run(run())
    assert widget.result is result


def test_ask_user_question_result_widget_handles_none_answers() -> None:
    result = AskUserQuestionResult.model_construct(answers=None, cancelled=False)
    widget = AskUserQuestionResultWidget(
        result, success=True, message="Questions answered", collapsed=False
    )

    async def run() -> None:
        await _render_widget(widget)

    import asyncio

    asyncio.run(run())
    assert widget.result is result


def test_history_reload_handles_none_tool_content(hacxgent_app) -> None:
    agent_loop = build_test_agent_loop()
    tool_call = ToolCall(
        id="call-1",
        index=0,
        function=FunctionCall(name="bash", arguments='{"command":"ideviceinfo"}'),
    )
    agent_loop.messages.extend(
        [
            LLMMessage(role=Role.user, content="run it"),
            LLMMessage(role=Role.assistant, content=None, tool_calls=[tool_call]),
            LLMMessage(
                role=Role.tool,
                content=None,
                name="bash",
                tool_call_id="call-1",
            ),
        ]
    )
    app = build_test_hacxgent_app(agent_loop=agent_loop)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            assert len(app.query(AssistantMessage)) == 0
            assert len(app.query(ToolCallMessage)) == 1
            assert len(app.query(ToolResultMessage)) == 1

    import asyncio

    asyncio.run(run())


def test_failed_bash_turn_allows_followup_user_turn() -> None:
    tool_call = ToolCall(
        id="call-2",
        index=0,
        function=FunctionCall(name="bash", arguments='{"command":"ideviceinfo"}'),
    )
    backend = FakeBackend(
        [
            [mock_llm_chunk(content="run bash", tool_calls=[tool_call])],
            [mock_llm_chunk(content="Tool failed, but continue.")],
            [mock_llm_chunk(content="Second turn complete.")],
        ]
    )
    agent_loop = build_test_agent_loop(backend=backend)
    import asyncio

    async def run() -> None:
        first_events = [event async for event in agent_loop.act("first")]
        second_events = [event async for event in agent_loop.act("continue")]

        assert len(backend.requests_messages) == 3
        assert any(getattr(event, "skipped", False) for event in first_events)
        assert any(getattr(event, "content", "") == "Second turn complete." for event in second_events)

    asyncio.run(run())
