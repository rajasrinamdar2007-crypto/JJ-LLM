import yaml

from llm_agent.detection_classes import load_rule_classes
from sih.rules.engine import RuleSyntaxError as EngineRuleSyntaxError
from sih.rules.engine import parse_rule as engine_parse_rule


class RuleSyntaxError(Exception):
    """Custom error for invalid rule YAML."""


_ALLOWED_PRIMITIVES = (str, int, float, bool, type(None))


class RuleValidator:
    def __init__(self, classes: list[str] | None = None):
        self.classes = classes or load_rule_classes()

    def parse_rule(self, yaml_content: str) -> str:
        try:
            rule = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            raise RuleSyntaxError(f"Invalid YAML format: {e}") from e

        if not isinstance(rule, dict):
            raise RuleSyntaxError("The rule must be a mapping at the top level.")

        self._check_primitives(rule)

        description = rule.get("description")
        if not isinstance(description, str) or not description.strip():
            raise RuleSyntaxError(
                "Missing 'description': every rule MUST have a non-empty description."
            )

        track = rule.get("track")
        if not isinstance(track, dict) or set(track) != {"class"}:
            raise RuleSyntaxError(
                "Missing or malformed 'track': every rule MUST have "
                f"track: {{class: ...}}, got {track!r}."
            )
        class_name = track["class"]
        if class_name not in self.classes:
            raise RuleSyntaxError(
                f"track.class {class_name!r} is not an allowed class. "
                f"Allowed classes: {', '.join(self.classes)}."
            )

        try:
            engine_parse_rule(rule)
        except EngineRuleSyntaxError as e:
            raise RuleSyntaxError(str(e)) from e

        return "Validation Passed! The rule is safe to use."

    def _check_primitives(self, value, path: str = "$") -> None:
        if isinstance(value, _ALLOWED_PRIMITIVES):
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise RuleSyntaxError(
                        f"{path}: mapping keys must be strings, got {key!r}"
                    )
                self._check_primitives(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self._check_primitives(child, f"{path}[{index}]")
        else:
            raise RuleSyntaxError(
                f"{path}: non-primitive value {value!r} of type {type(value).__name__}"
            )
