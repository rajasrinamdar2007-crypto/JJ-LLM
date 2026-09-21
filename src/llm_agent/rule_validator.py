import yaml


class RuleSyntaxError(Exception):
    """Custom error for invalid rule YAML."""


_ALLOWED_PRIMITIVES = (str, int, float, bool, type(None))


class RuleValidator:
    def parse_rule(self, yaml_content: str) -> str:
        try:
            rule = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            raise RuleSyntaxError(f"Invalid YAML format: {e}") from e

        if not isinstance(rule, dict):
            raise RuleSyntaxError("The rule must be a mapping at the top level.")

        self._check_primitives(rule)

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
