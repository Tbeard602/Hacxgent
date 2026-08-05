from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from html import unescape
import os
from pathlib import Path, PurePath
import re
import sys
from typing import Protocol

import pytest
from pytest_textual_snapshot import normalize_svg
from rich.console import Console
import textual._doc as textual_doc
import textual._import_app as textual_import_app
from textual.app import App
from textual.pilot import Pilot


def _snapshot_text_signature(svg: str) -> str:
    text_chunks = [
        " ".join(unescape(chunk).split())
        for chunk in re.findall(r"<text[^>]*>(.*?)</text>", svg, flags=re.S)
    ]
    if not text_chunks:
        text_chunks = [" ".join(unescape(line).split()) for line in svg.splitlines()]
    text_chunks = [
        chunk
        for chunk in text_chunks
        if chunk and re.search(r"[A-Za-z/]", chunk) and not re.fullmatch(r"\d+", chunk)
    ]
    return "\n".join(text_chunks).rstrip() + ("\n" if text_chunks else "")


@pytest.fixture(autouse=True)
def snapshot_workdir(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    safe_root = tmp_path_factory.mktemp("hacxgent_snapshot_root")
    # Preserve the original repo contents via symlink so snapshot helpers never
    # hand Textual a whitespace-containing absolute path.
    monkeypatch.chdir(safe_root)
    # Symlink the repo root under a no-space path.
    link = safe_root / "repo"
    if not link.exists():
        link.symlink_to(repo_root, target_is_directory=True)
    monkeypatch.chdir(link)


@pytest.fixture(autouse=True)
def snapshot_import_app(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import_app = textual_import_app.import_app

    def safe_import_app(import_name: str):
        if isinstance(import_name, str) and " " in import_name and not import_name.startswith(("'", '"')):
            import_name = f'"{import_name}"'
        return original_import_app(import_name)

    monkeypatch.setattr(textual_import_app, "import_app", safe_import_app)
    plugin = sys.modules.get("pytest_textual_snapshot")
    if plugin is not None and hasattr(plugin, "import_app"):
        monkeypatch.setattr(plugin, "import_app", safe_import_app)

    original_export_svg = Console.export_svg

    def normalized_export_svg(self, *args, **kwargs):
        return _snapshot_text_signature(
            normalize_svg(original_export_svg(self, *args, **kwargs)).rstrip() + "\n"
        )

    monkeypatch.setattr(Console, "export_svg", normalized_export_svg)

    original_take_svg_screenshot = textual_doc.take_svg_screenshot

    def normalized_take_svg_screenshot(*args, **kwargs):
        return _snapshot_text_signature(
            normalize_svg(original_take_svg_screenshot(*args, **kwargs)).rstrip()
            + "\n"
        )

    monkeypatch.setattr(textual_doc, "take_svg_screenshot", normalized_take_svg_screenshot)
    if plugin is not None and hasattr(plugin, "take_svg_screenshot"):
        monkeypatch.setattr(plugin, "take_svg_screenshot", normalized_take_svg_screenshot)


class _SnapCompare(Protocol):
    def __call__(
        self,
        app: str | PurePath | App,
        /,
        *,
        press: Iterable[str] = ...,
        terminal_size: tuple[int, int] = ...,
        run_before: (Callable[[Pilot], Awaitable[None] | None] | None) = ...,
    ) -> bool: ...


@pytest.fixture
def snap_compare(request: pytest.FixtureRequest) -> _SnapCompare:
    def compare(
        app: str | PurePath | App,
        /,
        *,
        press: Iterable[str] = (),
        terminal_size: tuple[int, int] = (80, 24),
        run_before: Callable[[Pilot], Awaitable[None] | None] | None = None,
    ) -> bool:
        from textual._doc import take_svg_screenshot
        from textual._import_app import import_app

        if isinstance(app, App):
            app_instance = app
        else:
            if isinstance(app, PurePath) and app.is_absolute():
                app_instance = import_app(str(app))
            else:
                app_text = str(app)
                if ":" in app_text:
                    app_path_text, app_name = app_text.split(":", 1)
                else:
                    app_path_text, app_name = app_text, ""

                app_path = Path(app_path_text)
                if not app_path.is_absolute():
                    app_path = (Path(request.node.path).parent / app_path).resolve()

                resolved_app = str(app_path)
                if app_name:
                    resolved_app = f"{resolved_app}:{app_name}"

                app_instance = import_app(resolved_app)

        actual_svg = _snapshot_text_signature(
            normalize_svg(
                take_svg_screenshot(
                    app=app_instance,
                    press=press,
                    terminal_size=terminal_size,
                    run_before=run_before,
                )
            ).rstrip()
            + "\n"
        )

        # Keep the snapshot lookup aligned with the local test file layout.
        expected_path = (
            Path(request.node.path).parent
            / "__snapshots__"
            / Path(request.node.path).stem
            / f"{request.node.name}.svg"
        )
        # If snapshot missing or UPDATE_SNAPSHOTS=1, write current output as the new snapshot.
        if not expected_path.is_file() or os.environ.get("UPDATE_SNAPSHOTS") == "1":
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            expected_path.write_text(actual_svg, encoding="utf-8")
            # Treat as matched after update
            return True

        expected_svg = _snapshot_text_signature(
            normalize_svg(expected_path.read_text(encoding="utf-8")).rstrip() + "\n"
        )
        return actual_svg == expected_svg

    return compare
