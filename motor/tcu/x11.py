"""Desktop adapter for Linux X11 (no AT-SPI): xdotool input, ImageMagick/scrot capture,
RapidOCR text. Accessibility is simply absent — `actionable_elements` reports nothing and
OCR carries the screen, the same mode the loop uses for a VM with no tree.
"""
from __future__ import annotations

import os
import subprocess
import time
from io import BytesIO
from pathlib import Path
from typing import Callable

from PIL import Image

from .models import Abort, AxNode, Field
from .platform_adapter import OcrLine

_SUB_TIMEOUT = 10  # every subprocess call is bounded — a hung helper can never freeze the loop
_rz = None  # lazy rapidocr engine


def _run(argv: list[str], timeout: float = _SUB_TIMEOUT, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(argv, timeout=timeout, capture_output=capture, check=False)


def _text(p: subprocess.CompletedProcess) -> str:
    return p.stdout.decode("utf-8", "replace")


def check_abort() -> None:
    p = _run(["xdotool", "getmouselocation", "--shell"])
    loc = dict(l.split("=", 1) for l in _text(p).split() if "=" in l)
    try:
        if int(float(loc.get("X", "999"))) < 4 and int(float(loc.get("Y", "999"))) < 4:
            raise Abort("mouse slammed to the top-left corner")
    except ValueError:
        return


def abort_hint() -> str:
    return "slam the mouse into the top-left corner to stop"


def sleep_watching(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        check_abort()
        time.sleep(min(0.2, end - time.time()))


def accessibility_trusted() -> bool:
    return False


def click_at(point: tuple[float, float]) -> None:
    x, y = int(round(point[0])), int(round(point[1]))
    _run(["xdotool", "mousemove", "--sync", str(x), str(y), "click", "1"])


def press(key: str, command: bool = False) -> None:
    keys = {"return": "Return", "escape": "Escape", "delete": "BackSpace", "tab": "Tab", "space": "space"}
    name = keys.get(key, key)
    argv = ["xdotool", "key"] + (["ctrl"] if command else []) + [name]
    _run(argv)


def type_text(text: str) -> None:
    _run(["xdotool", "type", "--clearmodifiers", "--delay", "12", "--", text], timeout=max(_SUB_TIMEOUT, 2 + len(text) * 0.03))


def clear_field() -> None:
    _run(["xdotool", "key", "ctrl+a"]); _run(["xdotool", "key", "Delete"])


def scroll(lines: int) -> None:
    button = "4" if lines > 0 else "5"
    _run(["xdotool", "click", "--repeat", str(min(abs(lines), 10) or 3), button])


def frontmost_app_and_pid() -> tuple[str, int]:
    p = _run(["xdotool", "getactivewindow", "getwindowname"])
    name = _text(p).strip() or "?"
    p2 = _run(["xdotool", "getactivewindow", "getwindowpid"])
    try:
        pid = int(_text(p2).strip())
    except ValueError:
        pid = 0
    return name, pid


def activate(app: str, timeout: float = 3.0) -> bool:
    p = _run(["xdotool", "search", "--onlyvisible", "--name", app, "windowactivate"])
    return p.returncode == 0


def open_url(browser: str, url: str) -> bool:
    return _run(["xdg-open", url], capture=False).returncode == 0


def browser_url(browser: str) -> str | None:
    return None  # no a11y tree → URL unreadable without one


def open_path(path: Path, as_text: bool = False) -> None:
    _run(["xdg-open", str(path)], capture=False)


def frontmost_window_bounds(pid: int | None = None) -> tuple[float, float, float, float] | None:
    p = _run(["xdotool", "getactivewindow", "getwindowgeometry", "--shell"])
    g = dict(l.split("=", 1) for l in _text(p).split() if "=" in l)
    try:
        return float(g["X"]), float(g["Y"]), float(g["WIDTH"]), float(g["HEIGHT"])
    except (KeyError, ValueError):
        return None


def screenshot() -> Image.Image:
    for argv in (["import", "-window", "root", "png:-"], ["scrot", "-o", "-z", "-"]):
        try:
            p = _run(argv)
            if p.returncode == 0 and p.stdout:
                return Image.open(BytesIO(p.stdout)).convert("RGB")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError("no screenshot tool worked (import / scrot)")


def display_scale(image: Image.Image) -> float:
    return 1.0


def _engine():
    global _rz
    if _rz is None:
        from rapidocr_onnxruntime import RapidOCR
        _rz = RapidOCR()
    return _rz


def recognize_text(image: Image.Image) -> list[OcrLine]:
    buf = BytesIO()
    image.save(buf, format="PNG")
    out, _ = _engine()(buf.getvalue())
    lines: list[OcrLine] = []
    for box, text, conf in out or []:
        xs = [pt[0] for pt in box]; ys = [pt[1] for pt in box]
        lines.append((text, float(conf), (min(xs), min(ys), max(xs), max(ys))))
    return lines


def focused_field() -> Field | None:
    return None  # no a11y


def actionable_elements(pid: int, w_pt: float, h_pt: float):
    return [], [], False


def ax_press(ref) -> bool:
    return False


def ax_focus(ref) -> bool:
    return False


def ax_set_value(ref, text: str) -> bool:
    return False


def ax_value(ref) -> str | None:
    return None
