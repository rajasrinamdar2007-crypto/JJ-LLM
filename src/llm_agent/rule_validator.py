import yaml

class RuleSyntaxError(Exception):
    """Custom error for invalid rule structures."""
    pass

class RuleValidator:
    def __init__(self):
        # We strictly define what is allowed based on the v2 grammar
        self.valid_operators = {"gt", "gte", "lt", "lte", "eq", "ne", "all", "any", "not"}
        self.valid_ops = {
            "is_visible", "confidence", "persisted_for", "count_in_window",
            "occurred_in_window", "avg_in_window", "bbox", "centroid", 
            "relative_vector", "area", "box_area", "distance", "magnitude", 
            "angle", "speed"
        }

    def parse_rule(self, yaml_content: str, expected_rule_id: str):
        try:
            rule = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            raise RuleSyntaxError(f"Invalid YAML format: {e}")

        # 1. Enforce top-level constraints
        if rule.get("schema_version") != 2:
            raise RuleSyntaxError("schema_version must be exactly 2.")
            
        if rule.get("rule_id") != expected_rule_id:
            raise RuleSyntaxError(f"rule_id must match the filename: '{expected_rule_id}'.")

        # 2. Enforce Step constraints
        steps = rule.get("steps", [])
        for step in steps:
            for step_type, step_content in step.items():
                if step_type not in ["check", "on_exit"]:
                    raise RuleSyntaxError(f"Invalid step: '{step_type}'. Only 'check' and 'on_exit' are allowed.")
                
                if step_type == "check":
                    self._validate_condition(step_content)
                elif step_type == "on_exit":
                    self._validate_on_exit(step_content)
                    
        return "Validation Passed! The rule is safe to use."

    def _validate_condition(self, condition: dict):
        """Recursively checks if condition operators and 'op' calls are valid."""
        if not isinstance(condition, dict):
            return
            
        for key, value in condition.items():
            if key in self.valid_operators:
                # If it's a list (like for 'all' or 'any'), check each child
                if isinstance(value, list):
                    for child in value:
                        self._validate_condition(child)
                else:
                    self._validate_condition(value)
            
            # If we hit an operation call, ensure the operation actually exists
            elif key == "op" and value not in self.valid_ops:
                raise RuleSyntaxError(f"Unknown operation: '{value}'. Must be one of {self.valid_ops}")
            
            elif isinstance(value, dict):
                self._validate_condition(value)

    def _validate_on_exit(self, on_exit_node: dict):
        """Ensures the on_exit actions are strictly 'emit' or 'mark: success'."""
        do_list = on_exit_node.get("do", [])
        for action in do_list:
            if isinstance(action, dict):
                if "emit" not in action and action.get("mark") != "success":
                    raise RuleSyntaxError(f"Invalid on_exit action: {action}. Must be 'emit' or 'mark: success'.")