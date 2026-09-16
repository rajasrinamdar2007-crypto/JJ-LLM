from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class Rule:
    id: str
    name: str
    query: str
    created_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "query": self.query,
            "created_at": self.created_at,
        }


@dataclass
class RulesStore:
    rules_dir: Path
    _rules: list[Rule] = field(default_factory=list, init=False)

    def load(self) -> list[Rule]:
        self._rules.clear()
        if not self.rules_dir.is_dir():
            self.rules_dir.mkdir(parents=True, exist_ok=True)
            return self._rules

        for p in sorted(self.rules_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text())
                self._rules.append(
                    Rule(
                        id=data.get("id", p.stem),
                        name=data.get("name", p.stem),
                        query=data.get("query", ""),
                        created_at=data.get("created_at", ""),
                    )
                )
            except (json.JSONDecodeError, KeyError):
                continue
        return self._rules

    def create(self, name: str, query: str) -> Rule:
        rule_id = uuid.uuid4().hex[:8]
        created = datetime.now(UTC).isoformat(timespec="seconds")
        rule = Rule(id=rule_id, name=name, query=query, created_at=created)
        self._rules.append(rule)
        rule_file = self.rules_dir / f"{rule_id}.json"
        rule_file.write_text(json.dumps(rule.to_dict(), indent=2) + "\n")
        return rule
