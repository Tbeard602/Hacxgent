from __future__ import annotations

import pytest
from textual.app import App

from hacxgent.cli.textual_ui.widgets.loading import LoadingWidget


class _LoadingWidgetApp(App[None]):
    def __init__(self, loading_widget: LoadingWidget) -> None:
        super().__init__()
        self.loading_widget = loading_widget

    def compose(self):
        yield self.loading_widget


@pytest.mark.parametrize("status", [None, ""])
def test_loading_widget_uses_default_status_when_missing(status: str | None) -> None:
    widget = LoadingWidget(status)

    assert widget.status == "Generating"
    assert isinstance(widget.status, str)
    widget._update_animation()


def test_loading_widget_renders_normal_status_text() -> None:
    widget = LoadingWidget("Working")

    assert widget.status == "Working"
    widget._update_animation()


def test_loading_widget_normalizes_none_during_animation() -> None:
    widget = LoadingWidget("Working")
    widget.status = None

    widget._update_animation()

    assert widget.status == ""
    assert "None" not in widget._build_status_text()


@pytest.mark.asyncio
async def test_loading_widget_starts_and_stops_spinner() -> None:
    widget = LoadingWidget("Working")

    async with _LoadingWidgetApp(widget).run_test() as pilot:
        await pilot.pause(0.15)
        assert widget._is_spinning
        assert widget._spinner_timer is not None

        widget.stop_spinning()
        assert not widget._is_spinning
        assert widget._spinner_timer is None


@pytest.mark.asyncio
async def test_loading_widget_closes_cleanly_while_spinning() -> None:
    widget = LoadingWidget("Working")

    async with _LoadingWidgetApp(widget).run_test() as pilot:
        await pilot.pause(0.15)
        assert widget._is_spinning

    assert widget._spinner_timer is None
