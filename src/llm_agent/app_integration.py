from __future__ import annotations

from pathlib import Path

from .gemini_translator import RuleTranslator
from .rule_validator import RuleSyntaxError, RuleValidator

RULES_DIR = Path(__file__).resolve().parent.parent.parent / "rules"


def create_and_save_rule(
    user_english: str, rule_name: str, rules_dir: Path | None = None
) -> bool:
    translator = RuleTranslator()
    validator = RuleValidator()
    rules_dir = rules_dir or RULES_DIR

    print(f"Asking Gemini to translate: '{user_english}'...")

    # 1. Ask Gemini to write the YAML code
    yaml_code = translator.translate_to_yaml(user_english, rule_name)

    # 2. Force the Validator to check the code before we save it
    try:
        validator.parse_rule(yaml_code)
        print("Validator says: The code looks perfect!")

        # 3. Create the 'rules' folder if it doesn't exist yet
        rules_dir.mkdir(parents=True, exist_ok=True)

        # 4. Save the open code as a real .yaml file
        file_path = rules_dir / f"{rule_name}.yaml"
        file_path.write_text(yaml_code, encoding="utf-8")

        print(f"Success! The rule is saved and ready at: {file_path}")
        return True

    except RuleSyntaxError as error:
        # If Gemini made a mistake, we catch it here and DO NOT save the file
        print(f"Uh oh, the Validator caught a mistake: {error}")
        return False
