"""Minimal typesafe-sdk surface used by the vendored motor: TypeSafeClient, Choice, Noul,
ChoiceAnswer, Score. Pure-stdlib shim over POST https://api.typesafe.ai/v1/systemone so the
vendored code never needs the real SDK wheel. Key: TYPESAFE_API_KEY or ~/bench/.typesafe_key.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any

API = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"


class TypeSafeError(RuntimeError):
    pass


def _key() -> str:
    k = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if k:
        return k
    path = os.path.expanduser("~/bench/.typesafe_key")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("PASTE_"):
                return line
    raise TypeSafeError("no TYPESAFE_API_KEY and ~/bench/.typesafe_key unreadable")


@dataclass
class Choice:
    instructions: Any
    criteria: dict[str, Any]

    def body(self) -> dict:
        return {"type": "choice", "instructions": self.instructions, "criteria": self.criteria}


@dataclass
class Noul:
    instructions: Any
    criteria: dict[str, Any] | None = None

    def body(self) -> dict:
        out = {"type": "noul", "instructions": self.instructions}
        if self.criteria:
            out["criteria"] = self.criteria
        return out


@dataclass
class Score:
    instructions: Any
    criteria: list[Any]

    def body(self) -> dict:
        return {"type": "score", "instructions": self.instructions, "criteria": self.criteria}


@dataclass
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    type: str = "choice"


@dataclass
class NoulAnswer:
    noul: float
    type: str = "noul"


@dataclass
class ScoreAnswer:
    score: float
    probabilities: list[float] = field(default_factory=list)
    confidence: float = 0.0
    type: str = "score"


@dataclass
class Reply:
    answers: dict[str, Any]
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)


def _answer(v: dict) -> Any:
    t = v.get("type")
    if t == "choice":
        return ChoiceAnswer(choice=v.get("choice", ""), probabilities=v.get("probabilities", {}) or {}, confidence=v.get("confidence", 0.0))
    if t == "noul":
        return NoulAnswer(noul=v.get("noul", 0.0))
    if t == "score":
        return ScoreAnswer(score=v.get("score", 0.0), probabilities=v.get("probabilities", []) or [], confidence=v.get("confidence", 0.0))
    raise TypeSafeError(f"unknown answer type {t!r}")


class TypeSafeClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL, timeout: float = 30.0):
        self._key = api_key or _key()
        self._model = model
        self._timeout = timeout

    def __enter__(self) -> "TypeSafeClient":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def system_one(self, *, state: Any, questions: dict[str, Any]) -> Reply:
        body = json.dumps({
            "state": state,
            "model": self._model,
            "questions": {k: (q.body() if hasattr(q, "body") else q) for k, q in questions.items()},
        }).encode()
        req = urllib.request.Request(
            API, data=body, method="POST",
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise TypeSafeError(f"systemone HTTP {e.code}: {e.read()[:300]!r}") from e
        return Reply(
            answers={k: _answer(v) for k, v in data.get("answers", {}).items()},
            model=data.get("model", ""),
            usage=data.get("usage", {}),
        )
