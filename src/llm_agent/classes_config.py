"""Resolve the detector class list the LLM may target when generating rules."""

from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CLASSES_FILE_NAME = "classes.txt"
CLASSES_ENV_VAR = "RULE_CLASSES"
_CLASS_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Detector classes emitted by the road-damage model (model.names: 0=crack, 1=pothole).
DEFAULT_CLASSES = ("crack", "pothole")


def load_allowed_classes() -> list[str]:
    """Return the allowed classes from $RULE_CLASSES, classes.txt, or the defaults.

    ``$RULE_CLASSES`` is a comma-separated list and wins over ``classes.txt``.
    ``classes.txt`` holds one class per line (blank lines and ``#`` comments
    ignored).  Entries are lowercased, de-duplicated, and must match
    ``[a-z][a-z0-9_]*``.
    """
    raw = os.environ.get(CLASSES_ENV_VAR)
    if raw:
        candidates = [c.strip() for c in raw.split(",") if c.strip()]
        return _normalise(candidates)

    classes_file = PROJECT_ROOT / CLASSES_FILE_NAME
    if classes_file.is_file():
        lines = [
            line.strip()
            for line in classes_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        return _normalise(lines)

    return list(DEFAULT_CLASSES)


def _normalise(candidates: list[str]) -> list[str]:
    classes: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip().lower()
        if _CLASS_RE.match(candidate) and candidate not in seen:
            classes.append(candidate)
            seen.add(candidate)
    return classes