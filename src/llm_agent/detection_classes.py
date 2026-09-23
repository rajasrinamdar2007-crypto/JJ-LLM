"""Allowed rule classes, sourced from the deployed JJ-object-detection config.

The detection agent is the source of truth for which object classes exist:
its ``config.toml`` maps raw model ``class_id`` -> contract class name.  The
LLM translator must only ever emit ``track.class`` values from this set, so a
generated rule can never reference a class the deployed pipeline does not know.

Falls back to the canonical ``{crack, pothole}`` pair when the detection repo
or its config is unavailable (e.g. running in a CI checkout without the
sibling repo).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

#: Fallback classes when the deployed config cannot be read.
DEFAULT_CLASSES = ("crack", "pothole")

_DETECTION_REPO = Path(__file__).resolve().parent.parent.parent.parent / "JJ-object-detection"
_CONFIG_NAME = "config.toml"


def load_rule_classes() -> list[str]:
    """Return the unique, sorted rule classes the deployed agent knows.

    Reads ``<detection repo>/config.toml`` ``[classes]`` map; values are the
    contract class names (duplicate class_ids collapse to one class).  Falls
    back to :data:`DEFAULT_CLASSES` on any read/parse error.
    """
    config_path = _DETECTION_REPO / _CONFIG_NAME
    try:
        with open(config_path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return list(DEFAULT_CLASSES)

    raw = data.get("classes")
    if not isinstance(raw, dict):
        return list(DEFAULT_CLASSES)

    values = {str(v) for v in raw.values() if isinstance(v, str) and v.strip()}
    return sorted(values) if values else list(DEFAULT_CLASSES)


__all__ = ["DEFAULT_CLASSES", "load_rule_classes"]