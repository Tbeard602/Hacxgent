from __future__ import annotations

from hacxgent.core.tools.builtins.read_file import (
    ReadFile,
    ReadFileArgs,
    ReadFileState,
    ReadFileToolConfig,
)
from hacxgent.core.tools.builtins.write_file import (
    WriteFile,
    WriteFileArgs,
    WriteFileConfig,
    WriteFileState,
)


def test_read_file_has_no_builtin_allowlist_or_denylist(tmp_path) -> None:
    target = tmp_path / "example.txt"
    target.write_text("hello\n", encoding="utf-8")
    tool = ReadFile(config=ReadFileToolConfig(), state=ReadFileState())

    assert tool.check_allowlist_denylist(ReadFileArgs(path=str(target))) is None
    assert tool.check_allowlist_denylist(ReadFileArgs(path=str(target), offset=1)) is None


def test_write_file_has_no_builtin_allowlist_or_denylist(tmp_path) -> None:
    tool = WriteFile(config=WriteFileConfig(), state=WriteFileState())

    assert (
        tool.check_allowlist_denylist(
            WriteFileArgs(path=str(tmp_path / "example.txt"), content="hello")
        )
        is None
    )
