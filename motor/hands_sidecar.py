#!/usr/bin/env python3
"""gray-hands sidecar: universal device-use over the vendored typesafe-computer-use motor.

Backends: `phone` (adb+uiautomator, no mobilerun), `computer` (X11: xdotool+scrot+RapidOCR),
`browser` (CDP — same motor, tcu browser backend). Every subprocess op is timeout-bounded.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tcu.platform_adapter import using  # noqa: E402
import tcu.adb as adb_backend  # noqa: E402
adb_backend.SERIAL = os.environ.get("HANDS_ADB_SERIAL") or None
import tcu.x11 as x11_backend  # noqa: E402
from tcu.models import Item  # noqa: E402

TOOLS = [
    {"name": "device_status", "description": "Frontmost app/window and interactive element count. target: phone|computer", "parameters": {"type": "object", "properties": {"target": {"type": "string", "description": "phone|computer (default: phone if a device is connected, else computer)"}}}, "snippet": "device_status"},
    {"name": "device_ui", "description": "List interactive elements (index, role, label, center) on the target screen", "parameters": {"type": "object", "properties": {"target": {"type": "string"}}}, "snippet": "device_ui"},
    {"name": "device_tap", "description": "Tap at x,y", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "target": {"type": "string"}}, "required": ["x", "y"]}, "snippet": "device_tap <x> <y>"},
    {"name": "device_tap_element", "description": "Tap element by index from device_ui", "parameters": {"type": "object", "properties": {"index": {"type": "integer"}, "target": {"type": "string"}}, "required": ["index"]}, "snippet": "device_tap_element <index>"},
    {"name": "device_type", "description": "Type text (optionally focus an element index first)", "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "element_index": {"type": "integer"}, "target": {"type": "string"}}, "required": ["text"]}, "snippet": "device_type <text>"},
    {"name": "device_swipe", "description": "Scroll/swipe up|down|left|right", "parameters": {"type": "object", "properties": {"direction": {"type": "string"}, "target": {"type": "string"}}}, "snippet": "device_swipe <direction>"},
    {"name": "device_press", "description": "Press a button: back|home|enter", "parameters": {"type": "object", "properties": {"button": {"type": "string"}, "target": {"type": "string"}}, "required": ["button"]}, "snippet": "device_press <button>"},
    {"name": "device_launch", "description": "Launch an app (package name on phone, name on computer)", "parameters": {"type": "object", "properties": {"package": {"type": "string"}, "target": {"type": "string"}}, "required": ["package"]}, "snippet": "device_launch <package>"},
    {"name": "device_apps", "description": "List apps (packages on phone; windows on computer)", "parameters": {"type": "object", "properties": {"target": {"type": "string"}}}, "snippet": "device_apps"},
    {"name": "device_screenshot", "description": "Save a screenshot; returns the file path", "parameters": {"type": "object", "properties": {"target": {"type": "string"}}}, "snippet": "device_screenshot"},
    {"name": "device_run", "description": "Autonomously run a natural-language task: Jev picks each action from the real UI state (a11y tree where present, OCR always). ~$0.0002/step.", "parameters": {"type": "object", "properties": {"task": {"type": "string", "description": "goal in plain language"}, "max_steps": {"type": "integer"}, "act": {"type": "boolean", "description": "default true; false = dry-run one step"}, "target": {"type": "string"}}, "required": ["task"]}, "snippet": "device_run <task>"},
]


def manifest() -> dict:
    return {"name": "hands", "version": "0.2.0", "protocol": "1.1",
            "tools": TOOLS, "commands": ["/hands"], "hooks": []}


def pick_backend(args: dict):
    t = (args.get("target") or "").lower()
    if t in ("computer", "desktop", "x11", "pc"):
        return "computer", x11_backend
    if t in ("browser",):
        return "browser", None  # CDP backend resolved lazily by run path
    if t in ("phone", "android", "adb"):
        return "phone", adb_backend
    if adb_backend.devices():
        return "phone", adb_backend
    return "computer", x11_backend


def _shot_path(tag: str) -> Path:
    p = Path(tempfile.gettempdir()) / f"hands-{tag}-{int(time.time())}.png"
    return p


def observe(backend, goal: str = ""):
    """Shared observe: the motor's own capture+perceive — OCR text blocks merged with
    the a11y tree where the backend has one, OCR-only where it does not. Returns
    (screen, items); item indices are stable within the call."""
    from tcu.perception import capture, perceive
    from tcu.config import MAX_OPTIONS
    with using(backend):
        screen = capture(browser="")
        # Empty goal poisons goal_echoes: "" is a substring of every line,
        # so everything filters as the command echo. A phrase never on screen
        # keeps the filter armed without eating real lines.
        items = perceive(screen, MAX_OPTIONS, goal or "inspect the screen")
    return screen, items


def tool_call(name: str, args: dict) -> dict:
    target_name, backend = pick_backend(args)

    if name == "device_run":
        return run_task(args, target_name)

    if backend is None:
        return err("browser target only supports device_run for now")

    try:
        if name in ("device_status", "device_ui"):
            screen, items = observe(backend)
            img = screen.image
            if name == "device_status":
                return ok(f"target: {target_name}\napp: {screen.app}\n{len(items)} on-screen items\nscreen: {img.width}x{img.height}\nurl: {screen.url or '-'}")
            if not items:
                return ok(f"target: {target_name}\napp: {screen.app}\nscreen is empty or unreadable — try device_screenshot")
            lines = []
            for it in items:
                cx, cy = it.center
                role = f"{it.role} " if it.role else ""
                lines.append(f"{it.index}. {role}'{it.text[:60]}' ({cx:.0f},{cy:.0f})")
            return ok(f"target: {target_name}\napp: {screen.app}\n" + "\n".join(lines[:120]))

        if name == "device_tap":
            x, y = args.get("x", -1), args.get("y", -1)
            if x < 0 or y < 0:
                return err("x and y required")
            backend.click_at((float(x), float(y)))
            return ok(f"tapped {x},{y} on {target_name}")

        if name == "device_tap_element":
            screen, items = observe(backend)
            i = args.get("index", -1)
            it = next((x for x in items if x.index == i), None)
            if it is None:
                return err(f"no element index {i}")
            ref = screen.ax_refs.get(i)
            if ref is not None and backend.ax_press(ref):
                pass
            else:
                backend.click_at(it.center)
            return ok(f"tapped [{i}] '{it.text[:40]}' on {target_name}")

        if name == "device_type":
            text = args.get("text", "")
            if not text:
                return err("text required")
            if "element_index" in args:
                screen, items = observe(backend)
                i = args["element_index"]
                it = next((x for x in items if x.index == i), None)
                if it is None:
                    return err(f"no element index {i}")
                ref = screen.ax_refs.get(i)
                if ref is not None and backend.ax_press(ref):
                    pass
                else:
                    backend.click_at(it.center)
                time.sleep(0.3)
            backend.type_text(text)
            return ok(f"typed {len(text)} chars on {target_name}")

        if name == "device_swipe":
            backend.scroll(1 if args.get("direction", "up") in ("up", "left") else -1)
            return ok(f"swiped {args.get('direction', 'up')} on {target_name}")

        if name == "device_press":
            b = args.get("button", "back")
            if target_name == "phone":
                keymap = {"back": "KEYCODE_BACK", "home": "KEYCODE_HOME", "enter": "KEYCODE_ENTER"}
                if b not in keymap:
                    return err("button must be back|home|enter")
                backend._adb(["shell", "input", "keyevent", keymap[b]])
            else:
                backend.press({"back": "alt+Left", "home": "Home", "enter": "Return"}[b] if b in ("back", "home", "enter") else b)
            return ok(f"pressed {b} on {target_name}")

        if name == "device_launch":
            pkg = args.get("package", "")
            if not pkg:
                return err("package required")
            if target_name == "phone":
                backend.activate(pkg)
            else:
                backend.activate(pkg)
            return ok(f"launched {pkg} on {target_name}")

        if name == "device_apps":
            if target_name == "phone":
                out = backend._adb(["shell", "pm", "list", "packages"]).stdout.decode("utf-8", "replace")
                return ok(out.replace("package:", "")[:6000])
            app, _ = backend.frontmost_app_and_pid()
            return ok(f"active window: {app}")

        if name == "device_screenshot":
            img = backend.screenshot()
            p = _shot_path(target_name)
            img.save(p)
            return ok(str(p))

        return err(f"unknown tool {name}")
    except Exception as e:
        return err(f"{type(e).__name__}: {e}")


def run_task(args: dict, target_name: str) -> dict:
    task = (args.get("task") or "").strip()
    if not task:
        return err("task required")
    steps = int(args.get("max_steps") or 15)
    steps = max(1, min(steps, 30))
    act = args.get("act", True)

    from tcu.runner import RunConfig, run
    from tcu.actions import Context
    from tcu import config as tcu_config

    out = Path(tempfile.gettempdir()) / f"hands-run-{int(time.time())}"
    cfg = RunConfig(goal=task, out=out, act=bool(act), steps=steps)

    def ctx_factory(typesafe, history):
        return Context(goal=task, browser=tcu_config.browser(), email=None,
                       typesafe=typesafe, writer=None, history=history, ask=None)

    backends = {"phone": adb_backend, "computer": x11_backend}
    backend = backends.get(target_name)
    if backend is None:
        return err(f"run not supported yet on target {target_name}")
    try:
        if target_name == "phone":
            adb_backend._require_device()
        with using(backend):
            state = run(cfg, ctx_factory)
        result = {"outcome": state.outcome, "steps": len(state.history),
                  "run_folder": str(out), "answer": getattr(state.answer, "text", None)}
        return ok(json.dumps(result, indent=2))
    except Exception as e:
        return err(f"run failed: {type(e).__name__}: {e}")


def ok(text: str) -> dict:
    return {"content": str(text)}


def err(text: str) -> dict:
    return {"content": str(text), "is_error": True}


def reply(id_, result):
    sys.stdout.write(json.dumps({"id": id_, "result": result}) + "\n")
    sys.stdout.flush()


def error_reply(id_, msg):
    sys.stdout.write(json.dumps({"id": id_, "error": {"code": "protocol", "message": msg}}) + "\n")
    sys.stdout.flush()


HELP = """hands — universal device control. Tools take target=phone|computer (auto: phone if adb device attached).
  device_status/ui/tap/tap_element/type/swipe/press/launch/apps/screenshot — atomic actions
  device_run task="..." [max_steps] [act=false] — Jev drives the device toward the goal
Backends: phone=adb+uiautomator, computer=X11 (xdotool+scrot+RapidOCR). Decider: TypeSafe Jev.
"""


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        if method == "plugin/shutdown":
            break
        rid = req.get("id")
        if rid is None:
            continue
        params = req.get("params") or {}
        if method == "plugin/manifest":
            reply(rid, manifest())
        elif method == "plugin/tools":
            reply(rid, {"tools": TOOLS})
        elif method == "tool/call":
            reply(rid, tool_call(params.get("name", ""), params.get("args") or {}))
        elif method == "command/run":
            reply(rid, {"text": HELP})
        else:
            error_reply(rid, f"unknown method {method}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
