from __future__ import annotations

import random
import time

import httpx
from google import genai
from google.genai import errors, types

from llm_agent.detection_classes import load_rule_classes
from llm_agent.env_config import resolve_api_key
from llm_agent.grammar_spec import build_grammar_spec

_RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3
#: Overall wall-clock budget for one translate call.  Bounding the *total*
#: (instead of "3 attempts x per-request timeout") is what stops a flaky
#: gateway from silently eating several minutes of the user's time.
_TOTAL_DEADLINE_S = 90.0
#: Per-read timeout for the streaming request.  With streaming this is a
#: per-chunk read timeout, so a hung gateway surfaces in seconds instead of
#: blocking the whole generation for a single fixed 60s envelope.  (A lower
#: number also lowers the X-Server-Timeout header the SDK sends to Google's
#: gateway, which is what the 504s were hanging on.)
_HTTP_TIMEOUT_MS = 45_000
#: Overall wall-clock budget for one translate call.  Bounding the total
#: (instead of 3 * per-request timeout) is what stops a flaky gateway from
#: silently eating several minutes of the user's time.
_TOTAL_DEADLINE_S = 90.0
#: Per-request wait.  With streaming this is a per-read timeout, so a hung
#: gateway surfaces quickly instead of blocking for the full 60 s.
_HTTP_TIMEOUT_MS = 30_000

_EXAMPLE_RULE = """schema_version: 2
rule_id: {rule_name}
description: >
  A {class_name} appears across at least 5 frames with mean confidence >= 0.60,
  and the rule succeeds when it leaves the frame.
track:
  class: {class_name}
steps:
  - check:
      gte:
        left: {{ op: persisted_for, entity: "$entity", window: {{ frames: 5 }} }}
        right: 5
  - check:
      gte:
        left: {{ op: avg_in_window, entity: "$entity", metric: confidence, window: {{ frames: 5 }} }}
        right: 0.6
  - on_exit:
      leave_frames: 1
      do:
        - emit:
            event_type: {class_name}_persistent_confirmed
            confidence: {{ op: avg_in_window, entity: "$entity", metric: confidence, window: {{ frames: 5 }} }}
            payload:
              tracker_id: "$entity"
              frames_seen: {{ op: persisted_for, entity: "$entity", window: {{ frames: 60 }} }}
        - mark: success"""


class RuleTranslator:
    def __init__(self, classes: list[str] | None = None):
        # This turns on the Gemini brain
        api_key = resolve_api_key()
        self.client = genai.Client(
            api_key=api_key, http_options={"timeout": _HTTP_TIMEOUT_MS}
        )
        self.classes = classes or load_rule_classes()

        # A strict, machine-derived grammar so Gemini writes rules the engine can parse.
        self.system_prompt = f"""
You write rule definitions for a road-damage detection system.
You MUST output ONLY valid YAML matching the grammar below.
Do not include markdown blocks like ```yaml. Do not say 'Here is your code'. Do not add prose.

Allowed classes for track.class, class_name, and entity references:
{", ".join(self.classes) if self.classes else "NONE - no classes configured"}

{_build_grammar_block()}

MANDATORY -- every rule MUST include exactly these top-level keys:
- description: a non-empty YAML string explaining what the rule detects.
- track: with class chosen ONLY from the allowed classes list above.
- schema_version: 2
- rule_id: the requested id.
- steps: a non-empty list.

FORBIDDEN -- never use these legacy/wrong keys (the engine rejects them):
target_label, target_class, operator, action, trigger, condition, output,
or any step shaped like {{ check: {{ op: ..., value: ... }} }}. Conditions must
use the comparison / logic forms from the Strict grammar, and on_exit must use
{{ do: [emit, mark] }} exactly as shown.

Example rule (use only as a template -- adapt steps to the requested behavior,
and replace the rule_id/class with the ones you are asked for):
{_example_rule()}
""".strip()
        self.model = "gemini-3.6-flash"

    def translate_to_yaml(
        self, english_sentence: str, rule_name: str, on_status=None
    ) -> str:
        prompt = (
            f"Make a rule for this: '{english_sentence}'. The rule_id must be: "
            f"{rule_name} (equal to the filename stem). track.class must be one of "
            f"the allowed classes and MUST be present. The description top-level "
            f"key is MANDATORY."
        )
        if on_status:
            on_status("Asking Gemini to translate...")

        deadline = time.monotonic() + _TOTAL_DEADLINE_S
        for attempt in range(_MAX_ATTEMPTS):
            if on_status:
                on_status(f"Attempt {attempt + 1}/{_MAX_ATTEMPTS}...")
            try:
                stream = self.client.models.generate_content_stream(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=self.system_prompt,
                        temperature=0.0,
                    ),
                )
                parts = []
                for chunk in stream:
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            f"Gemini exceeded the {_TOTAL_DEADLINE_S:.0f}s overall deadline."
                        )
                    piece = getattr(chunk, "text", None)
                    if piece:
                        parts.append(piece)
                        if on_status:
                            on_status(
                                f"Streaming rule (~{len(''.join(parts))} chars)..."
                            )
            except (httpx.HTTPError, TimeoutError, OSError) as exc:
                if attempt < _MAX_ATTEMPTS - 1:
                    if on_status:
                        on_status(
                            f"Network issue ({exc.__class__.__name__}); retrying..."
                        )
                    self._backoff(attempt)
                    continue
                raise
            except errors.APIError as exc:
                if exc.code in _RETRYABLE_CODES and attempt < _MAX_ATTEMPTS - 1:
                    if on_status:
                        on_status(
                            f"Gemini busy ({exc.code} {exc.status}); retrying in a moment..."
                        )
                    else:
                        print(
                            f"Gemini busy ({exc.code} {exc.status}); retrying in a moment..."
                        )
                    self._backoff(attempt)
                    continue
                raise

            text = "".join(parts).strip()
            if not text:
                raise RuntimeError(
                    "Gemini returned no text (empty or blocked response)."
                )
            return text
        raise RuntimeError(f"Gemini request failed after {_MAX_ATTEMPTS} attempts.")

    def _backoff(self, attempt: int) -> None:
        delay = min(2**attempt, 8) + random.uniform(0, 1)
        time.sleep(delay)


def _build_grammar_block() -> str:
    grammar = build_grammar_spec()
    block = "Strict grammar:\n" + grammar
    block += (
        "\n\nAllowed classes: see above list; ONLY these class names are permitted."
    )
    return block


def _example_rule() -> str:
    return _EXAMPLE_RULE.format(rule_name="example_rule", class_name="pothole")
