from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from sih.rules.engine import (
    RuleSyntaxError as EngineRuleSyntaxError,
)
from sih.rules.engine import load_rule_file, parse_rule
from sih.rules.types import Rule
from sih.verification import SimilarityFinding, find_for_candidate

from .detection_classes import load_rule_classes
from .gemini_translator import RuleTranslator
from .rule_validator import RuleSyntaxError, RuleValidator

RULES_DIR = Path(__file__).resolve().parent.parent.parent / "LM-rules"

#: Author-side fixtures that must never be treated as real comparison targets.
_EXCLUDED_FIXTURES = ("contradictory_area.yaml", "unparseable_broken.yaml")


@dataclass(frozen=True)
class SaveOutcome:
    """Result of a generation + save attempt surfaced back to the TUI."""

    saved: bool
    rule_id: str = ""
    file_path: Path | None = None
    #: Stage 1 near-duplicate flags against existing LM-rules.  Informational
    #: only -- a flag never blocks the save; the human reviewer decides.
    similarities: list[SimilarityFinding] = field(default_factory=list)


def _load_existing(rules_dir: Path, skip_stem: str) -> list[Rule]:
    """Load every parseable rule already on disk as a dedup corpus."""
    existing: list[Rule] = []
    for path in sorted(rules_dir.glob("*.yaml")):
        if path.name in _EXCLUDED_FIXTURES:
            continue
        if path.stem == skip_stem:
            continue  # the file we are about to write, if it pre-exists
        try:
            existing.append(load_rule_file(path))
        except (EngineRuleSyntaxError, OSError):
            continue  # broken/invalid rules are not valid comparison targets
    return existing


def create_and_save_rule(
    user_english: str,
    rule_name: str,
    rules_dir: Path | None = None,
    on_status=None,
) -> SaveOutcome:
    classes = load_rule_classes()
    translator = RuleTranslator(classes=classes)
    validator = RuleValidator(classes=classes)
    rules_dir = rules_dir or RULES_DIR

    if on_status:
        on_status(f"Translating: '{user_english}'...")
    else:
        print(f"Asking Gemini to translate: '{user_english}'...")

    try:
        # 1. Ask Gemini to write the YAML code
        yaml_code = translator.translate_to_yaml(
            user_english, rule_name, on_status=on_status
        )

        # 2. Force the Validator to check the code before we save it
        validator.parse_rule(yaml_code)
        print("Validator says: The code looks perfect!")

        # 3. Save the open code as a real .yaml file
        rules_dir.mkdir(parents=True, exist_ok=True)
        file_path = rules_dir / f"{rule_name}.yaml"
        file_path.write_text(yaml_code, encoding="utf-8")

        # 4. Stage 1: flag near-duplicates against existing rules.  Best-effort
        #    and informational -- it never blocks the save (a flag is for a
        #    human reviewer), so a candidate that can't be re-parsed for dedup
        #    still saves with no flag rather than failing.
        similarities: list[SimilarityFinding] = []
        try:
            candidate = parse_rule(yaml.safe_load(yaml_code))
            similarities = find_for_candidate(
                candidate, _load_existing(rules_dir, rule_name)
            )
        except (EngineRuleSyntaxError, yaml.YAMLError, OSError):
            similarities = []

        print(f"Success! The rule is saved and ready at: {file_path}")
        for finding in similarities:
            print(
                f"  NOTE: possible near-duplicate of {finding.existing} "
                f"(similarity {finding.similarity:.2f})"
            )
        return SaveOutcome(
            saved=True,
            rule_id=rule_name,
            file_path=file_path,
            similarities=similarities,
        )
    except RuleSyntaxError as error:
        # If Gemini made a mistake, we catch it here and DO NOT save the file
        print(f"Uh oh, the Validator caught a mistake: {error}")
        return SaveOutcome(saved=False)
