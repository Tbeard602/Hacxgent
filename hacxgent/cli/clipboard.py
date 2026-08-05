from __future__ import annotations

import base64
import os
import shutil
import subprocess

import pyperclip
from textual.app import App

_PREVIEW_MAX_LENGTH = 40


def _copy_osc52(text: str) -> None:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    osc52_seq = f"\033]52;c;{encoded}\a"
    if os.environ.get("TMUX"):
        osc52_seq = f"\033Ptmux;\033{osc52_seq}\033\\"

    # OSC 52 works by writing to stdout/tty
    try:
        if os.name == 'nt':
             # On Windows, try writing to stdout directly
             print(osc52_seq, end="", flush=True)
        else:
            with open("/dev/tty", "w") as tty:
                tty.write(osc52_seq)
                tty.flush()
    except Exception:
        # Fallback to pyperclip
        pyperclip.copy(text)


def _copy_primary_selection(text: str) -> None:
    if os.environ.get("WAYLAND_DISPLAY"):
        if not shutil.which("wl-copy"):
            raise RuntimeError("wl-copy is not available for primary selection")
        subprocess.run(
            ["wl-copy", "--primary"], input=text, text=True, check=True
        )
        return

    if shutil.which("xclip"):
        subprocess.run(
            ["xclip", "-selection", "primary", "-in"],
            input=text,
            text=True,
            check=True,
        )
        return

    if shutil.which("xsel"):
        subprocess.run(
            ["xsel", "--primary", "--input"], input=text, text=True, check=True
        )
        return

    raise RuntimeError("No primary selection helper is available")


def _shorten_preview(texts: list[str]) -> str:
    dense_text = "⏎".join(texts).replace("\n", "⏎")
    if len(dense_text) > _PREVIEW_MAX_LENGTH:
        return f"{dense_text[: _PREVIEW_MAX_LENGTH - 1]}…"
    return dense_text


def copy_selection_to_clipboard(app: App, show_toast: bool = True) -> None:
    selected_texts = []

    for widget in app.query("*"):
        if not hasattr(widget, "text_selection") or not widget.text_selection:
            continue

        selection = widget.text_selection

        try:
            result = widget.get_selection(selection)
        except Exception:
            continue

        if not result:
            continue

        selected_text, _ = result
        if selected_text.strip():
            selected_texts.append(selected_text)

    if not selected_texts:
        return

    combined_text = "\n".join(selected_texts)

    osc52_error: Exception | None = None
    primary_error: Exception | None = None

    try:
        _copy_osc52(combined_text)
    except Exception as exc:
        osc52_error = exc

    try:
        _copy_primary_selection(combined_text)
    except Exception as exc:
        primary_error = exc

    if osc52_error and primary_error:
        app.notify(
            "Failed to copy - clipboard not available", severity="warning", timeout=3
        )
        return

    if show_toast:
        app.notify(
            f'"{_shorten_preview(selected_texts)}" copied to clipboard',
            severity="information",
            timeout=2,
            markup=False,
        )
