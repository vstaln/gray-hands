"""Desktop adapter for Android over raw `adb` — no mobilerun, no extra daemons.

Tree: `uiautomator dump` XML -> AxNode list in tcu vocabulary. Android widget classes map onto
the same role words the loop speaks; `focused="true"` nodes become the Field. Capture:
`exec-out screencap -p`. Input: `input tap|swipe|text|keyevent`. Every subprocess call is
bounded by ADB_TIMEOUT — a stuck device reports an error, it never freezes the sidecar.
"""
from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path

from PIL import Image

from .models import AxNode, Field
from .platform_adapter import OcrLine

ADB_TIMEOUT = 12
SERIAL = None  # optional -s target


def _adb(argv: list[str], timeout: float = ADB_TIMEOUT, binary: bool = False) -> subprocess.CompletedProcess:
    cmd = ["adb"] + ([ "-s", SERIAL ] if SERIAL else []) + argv
    try:
        return subprocess.run(cmd, timeout=timeout, capture_output=True, check=False)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"adb {argv[0]} timed out after {timeout}s") from e
    except FileNotFoundError as e:
        raise RuntimeError("adb not installed") from e


def devices() -> list[str]:
    out = _adb(["devices"]).stdout.decode("utf-8", "replace")
    return [l.split()[0] for l in out.splitlines()[1:] if l.strip() and l.split()[-1] == "device"]


def _require_device() -> None:
    if not devices():
        raise RuntimeError("no connected Android devices found")


def check_abort() -> None:
    return None


def abort_hint() -> str:
    return "Ctrl-C on the host stops the run"


def sleep_watching(seconds: float) -> None:
    time.sleep(seconds)


def accessibility_trusted() -> bool:
    return True


def click_at(point: tuple[float, float]) -> None:
    _adb(["shell", "input", "tap", str(int(point[0])), str(int(point[1]))])


_KEYS = {"return": "KEYCODE_ENTER", "escape": "KEYCODE_BACK", "delete": "KEYCODE_DEL", "tab": "KEYCODE_TAB", "space": "KEYCODE_SPACE"}


def press(key: str, command: bool = False) -> None:
    _adb(["shell", "input", "keyevent", _KEYS.get(key, key.upper())])


def type_text(text: str) -> None:
    esc = text.replace("\\", "\\\\").replace('"', '\\"').replace(" ", "%s").replace("&", "\\&").replace("'", "\\'").replace(";", "\\;").replace("<", "\\<").replace(">", "\\>").replace("|", "\\|").replace("(", "\\(").replace(")", "\\)")
    _adb(["shell", "input", "text", esc], timeout=max(ADB_TIMEOUT, 4 + len(text) * 0.1))


def clear_field() -> None:
    _adb(["shell", "input", "keyevent", "KEYCODE_MOVE_END"])
    for _ in range(50):
        _adb(["shell", "input", "keyevent", "KEYCODE_DEL"])


def scroll(lines: int) -> None:
    w, h = screen_size()
    cx, cy = w // 2, h // 2
    dy = h // 3
    if lines > 0:  # scroll content up = reveal below
        _adb(["shell", "input", "swipe", str(cx), str(cy + dy), str(cx), str(cy - dy), "300"])
    else:
        _adb(["shell", "input", "swipe", str(cx), str(cy - dy), str(cx), str(cy + dy), "300"])


def screen_size() -> tuple[int, int]:
    out = _adb(["shell", "wm", "size"]).stdout.decode("utf-8", "replace")
    m = re.search(r"(\d+)x(\d+)", out)
    return (int(m[1]), int(m[2])) if m else (1080, 2400)


def frontmost_app_and_pid() -> tuple[str, int]:
    out = _adb(["shell", "dumpsys", "window", "displays"]).stdout.decode("utf-8", "replace")
    m = re.search(r"mCurrentFocus=\S+\s+\d+\s+([\w.]+)/", out) or re.search(r"mCurrentFocus=\S+\s+([\w.]+)/", out)
    app = m.group(1) if m else "?"
    pid_s = _adb(["shell", "pidof", app]).stdout.decode().strip() if m else ""
    try:
        pid = int(pid_s.split()[0])
    except (ValueError, IndexError):
        pid = 0
    return app, pid


def activate(app: str, timeout: float = 3.0) -> bool:
    return _adb(["shell", "monkey", "-p", app, "-c", "android.intent.category.LAUNCHER", "1"]).returncode == 0


def open_url(browser: str, url: str) -> bool:
    return _adb(["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url]).returncode == 0


def browser_url(browser: str) -> str | None:
    return None


def open_path(path: Path, as_text: bool = False) -> None:
    _adb(["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", f"file://{path}"])


def frontmost_window_bounds(pid: int | None = None) -> tuple[float, float, float, float] | None:
    w, h = screen_size()
    return (0.0, 0.0, float(w), float(h))


def screenshot() -> Image.Image:
    _require_device()
    p = _adb(["exec-out", "screencap", "-p"], timeout=20)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError("screencap failed: " + p.stderr.decode("utf-8", "replace")[:200])
    return Image.open(BytesIO(p.stdout.replace(b"\r\n", b"\n"))).convert("RGB")


def display_scale(image: Image.Image) -> float:
    return 1.0


_rz = None


def recognize_text(image: Image.Image) -> list[OcrLine]:
    global _rz
    if _rz is None:
        from rapidocr_onnxruntime import RapidOCR
        _rz = RapidOCR()
    buf = BytesIO()
    image.save(buf, format="PNG")
    out, _ = _rz(buf.getvalue())
    lines: list[OcrLine] = []
    for box, text, conf in out or []:
        xs = [pt[0] for pt in box]; ys = [pt[1] for pt in box]
        lines.append((text, float(conf), (min(xs), min(ys), max(xs), max(ys))))
    return lines


def _dump_xml() -> ET.Element | None:
    _adb(["shell", "uiautomator", "dump", "/sdcard/window_dump.xml"], timeout=15)
    p = _adb(["exec-out", "cat", "/sdcard/window_dump.xml"], timeout=10)
    if p.returncode != 0 or not p.stdout:
        return None
    try:
        return ET.fromstring(p.stdout.decode("utf-8", "replace"))
    except ET.ParseError:
        return None


_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

_ROLES = {
    "android.widget.Button": "AXButton",
    "android.widget.ImageButton": "AXButton",
    "android.widget.EditText": "AXTextField",
    "android.widget.CheckBox": "AXCheckBox",
    "android.widget.Switch": "AXCheckBox",
    "android.widget.RadioButton": "AXRadioButton",
    "android.widget.Spinner": "AXComboBox",
    "android.widget.SeekBar": "AXSlider",
    "android.widget.TabWidget": "AXTab",
    "android.widget.TextView": "AXStaticText",
}


def _label(n: ET.Element) -> str:
    return (n.get("content-desc") or n.get("text") or n.get("resource-id", "").split("/")[-1]).strip()


def _nodes(el: ET.Element) -> list[AxNode]:
    out: list[AxNode] = []
    for n in el.iter("node"):
        cls = n.get("class", "")
        b = _BOUNDS.match(n.get("bounds", ""))
        if not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        clickable = n.get("clickable") == "true" or cls in _ROLES and _ROLES[cls] != "AXStaticText"
        label = _label(n)
        if not clickable and not label:
            continue
        role = _ROLES.get(cls, "AXStaticText")
        if n.get("password") == "true":
            role = "AXSecureTextField"
        ref = {"bounds": (x1, y1, x2, y2), "resource_id": n.get("resource-id", ""), "node": n}
        out.append(AxNode(role=role, label=label, x=x1, y=y1, w=x2 - x1, h=y2 - y1, pressable=clickable, ref=ref))
    return out


def actionable_elements(pid: int, w_pt: float, h_pt: float):
    root = _dump_xml()
    if root is None:
        return [], [], False
    return _nodes(root), [], False


def focused_field() -> Field | None:
    root = _dump_xml()
    if root is None:
        return None
    for n in root.iter("node"):
        if n.get("focused") == "true":
            b = _BOUNDS.match(n.get("bounds", ""))
            if not b:
                continue
            x1, y1, x2, y2 = map(int, b.groups())
            cls = n.get("class", "")
            role = "AXSecureTextField" if n.get("password") == "true" else _ROLES.get(cls, "AXTextField")
            return Field(role=role, label=_label(n), placeholder="", value=n.get("text", ""),
                         x=x1, y=y1, w=x2 - x1, h=y2 - y1, ref={"bounds": (x1, y1, x2, y2)})
    return None


def ax_press(ref) -> bool:
    if isinstance(ref, dict) and "bounds" in ref:
        x1, y1, x2, y2 = ref["bounds"]
        click_at(((x1 + x2) / 2, (y1 + y2) / 2))
        return True
    return False


def ax_focus(ref) -> bool:
    return ax_press(ref)


def ax_set_value(ref, text: str) -> bool:
    if ax_focus(ref):
        time.sleep(0.3)
        clear_field()
        type_text(text)
        return True
    return False


def ax_value(ref) -> str | None:
    n = ref.get("node") if isinstance(ref, dict) else None
    return n.get("text") if n is not None else None
