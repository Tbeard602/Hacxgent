from __future__ import annotations

import asyncio

import pytest
from textual.widgets.markdown import MarkdownFence

from hacxgent.cli.textual_ui.app import HacxgentApp
from hacxgent.cli.textual_ui.widgets.loading import LoadingWidget
from hacxgent.cli.textual_ui.widgets.messages import AssistantMessage
from hacxgent.core.types import LLMChunk, LLMMessage, LLMUsage, Role
from tests.conftest import build_test_agent_loop, build_test_hacxgent_app
from tests.stubs.fake_backend import FakeBackend


class BlockingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def complete(self, **kwargs):  # type: ignore[override]
        self.release_task = asyncio.create_task(self.release.wait())
        await self.release.wait()
        return LLMChunk(
            message=LLMMessage(
                role=Role.assistant,
                content="```python\nprint('hello')\n```",
            ),
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
        )


@pytest.mark.asyncio
async def test_startup_mounts_textual_ansi_and_renders_markdown() -> None:
    backend = BlockingBackend()
    agent_loop = build_test_agent_loop(backend=backend)
    app: HacxgentApp = build_test_hacxgent_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        assert app.theme == "textual-ansi"

        turn_task = asyncio.create_task(app._handle_agent_loop_turn("show me code"))
        await pilot.pause(0.2)

        assert isinstance(app.query_one(LoadingWidget), LoadingWidget)
        assert app.query(LoadingWidget)

        backend.release.set()
        await turn_task
        await pilot.pause(0.2)

        assistant_messages = list(app.query(AssistantMessage))
        assert assistant_messages
        assert app.query(MarkdownFence)

