from __future__ import annotations

from textual.app import ComposeResult

from hacxgent.cli.textual_ui.widgets.status_message import StatusMessage


class TeleportMessage(StatusMessage):
    def __init__(self) -> None:
        super().__init__()
        self.add_class("teleport-message")
        self._initial_text = "Teleporting..."

    def set_status(self, status: str) -> None:
        self._initial_text = status
        self.stop_spinning(success=True)

    def set_complete(self, url: str) -> None:
        self._initial_text = f"Teleported to Nuage: {url}"
        self.stop_spinning(success=True)

    def set_error(self, error_message: str) -> None:
        self._initial_text = f"Teleport failed: {error_message}"
        self.stop_spinning(success=False)

    def compose(self) -> ComposeResult:
        return super().compose()

