from __future__ import annotations

import random
import time

import httpx
from google import genai
from google.genai import errors, types

from llm_agent.env_config import resolve_api_key

_RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


class RuleTranslator:
    def __init__(self):
        # This turns on the Gemini brain
        api_key = resolve_api_key()
        self.client = genai.Client(api_key=api_key, http_options={"timeout": 60_000})

        # These are the strict rules we force Gemini to follow so it doesn't break The Watcher
        self.system_prompt = """
        You are a translator for a road-damage detection system.
        You MUST output ONLY valid YAML matching schema_version: 2.
        Do not include markdown blocks like ```yaml. Do not say 'Here is your code'.
        Constraints:
        - rule_id must match the provided filename exactly.
        - Steps must only contain 'check' and 'on_exit'.
        - Valid ops are: is_visible, confidence, persisted_for, avg_in_window, bbox.
        """
        self.model = "gemini-3.6-flash"

    def translate_to_yaml(self, english_sentence: str, rule_name: str) -> str:
        prompt = (
            f"Make a rule for this: '{english_sentence}'. The rule_id is: {rule_name}"
        )
        print("Asking Gemini to translate...")

        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=self.system_prompt,
                        temperature=0.0,
                    ),
                )
            except (httpx.HTTPError, TimeoutError, OSError):
                if attempt < _MAX_ATTEMPTS - 1:
                    self._backoff(attempt)
                    continue
                raise
            except errors.APIError as exc:
                if exc.code in _RETRYABLE_CODES and attempt < _MAX_ATTEMPTS - 1:
                    print(
                        f"Gemini busy ({exc.code} {exc.status}); retrying in a moment..."
                    )
                    self._backoff(attempt)
                    continue
                raise

            text = getattr(response, "text", None)
            if not text:
                raise RuntimeError(
                    "Gemini returned no text (empty or blocked response)."
                )
            return text.strip()
        raise RuntimeError("Gemini request failed after all retries.")

    def _backoff(self, attempt: int) -> None:
        delay = min(2**attempt, 8) + random.uniform(0, 1)
        time.sleep(delay)
