<p align="center">
  <img src="assets/gray-logo.svg" alt="gray" width="96">
</p>
<h1 align="center">gray-hands</h1>
<p align="center">Drive an Android phone from a gray session — UI-tree tools plus an autonomous Jev task loop.</p>
<p align="center">
  <a href="https://github.com/vstaln/gray-hands/blob/main/LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="gray plugin" src="https://img.shields.io/badge/gray-plugin-7aa2f7.svg">
  <img alt="rust" src="https://img.shields.io/badge/built%20with-rust-orange.svg">
</p>

Drive a connected Android device from a [gray](https://github.com/vstaln/gray) session. Every screen
interaction goes through `mobilerun device` (the mobilerun Portal CLI over
adb); the `device_run` tool asks Jev (TypeSafe System One) to pick each action
from the real UI tree, so a natural-language task runs autonomously at ~2–3s
per step.

A sidecar plugin for [gray](https://github.com/vstaln/gray).

## Tools

| tool | what it does |
|---|---|
| `device_status` | foreground app + clickable element count |
| `device_ui` | the UI tree: every clickable element's index, class, label, center coords |
| `device_tap {x, y}` | tap raw screen coordinates |
| `device_tap_element {index}` | tap an element by its `device_ui` index |
| `device_type {text, element_index?}` | type into a field (optional tap-to-focus first) |
| `device_swipe {direction}` | scroll `up`/`down`/`left`/`right` |
| `device_press {button}` | `back` / `home` / `enter` |
| `device_launch {package}` | launch an app by package name |
| `device_apps` | list installed apps |
| `device_screenshot` | take a screenshot, returns the saved file path |
| `device_run {task, max_steps?}` | autonomous loop: Jev reads the live UI tree and picks each action (tap / type / swipe / launch / back / wait / done / stuck); `max_steps` defaults to 15, capped at 30 |

Quote literal text to type inside the task string (e.g.
`device_run "open settings and search for 'wifi'"`).

`/hands` mirrors the tools inside a session:

```text
/hands [ui|status|apps|screenshot|tap x y|swipe dir|press btn|launch pkg|type text|run task]
```

## Requirements

- `mobilerun` on `PATH`, with a device reachable over adb
- `TYPESAFE_API_KEY` (or a key line in `~/bench/.typesafe_key`) for
  `device_run` — the other tools need no key

## Wire

`plugin/manifest` · `tool/call` (11 tools) · `command/run` (`/hands`) ·
`plugin/shutdown` — protocol 1.1. No capabilities, no hooks.

## Install

```sh
gray plugin install hands
```

## Develop

```sh
cargo test
gray account check      # entry point + manifest handshake
gray account publish    # check → build → release → publish to the gray registry
```

Bump `version` in `Cargo.toml` before each `publish`; the registry refuses to
republish a version.

---

Part of the [gray](https://github.com/vstaln/gray) plugin ecosystem —
the open-source AI agent harness. <https://gray.alignment.id>
