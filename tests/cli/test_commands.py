from __future__ import annotations

from hacxgent.cli.commands import CommandRegistry


def test_vision_command_is_registered() -> None:
    registry = CommandRegistry()

    command = registry.find_command("/vision")

    assert command is not None
    assert command.handler == "_handle_vision_cmd"
    assert "/vision" in command.aliases
