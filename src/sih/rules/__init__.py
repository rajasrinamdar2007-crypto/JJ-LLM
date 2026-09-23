"""Rule engine: stateful, LLM-authorable YAML rules over the shared framebuffer.

Rules are declarative YAML (schema version 2), one file per rule, that latch
every BoT-SORT ``tracker_id`` of a class and advance per-instance records across
frames: ``check`` steps latch-on-truth, and ``on_exit`` fires once when the
tracked entity leaves the view — emitting events and ``mark: success``.  Every
operation and step result carries an executed-successfully boolean.
"""

from sih.rules.engine import (
    RuleRegistry,
    RuleSyntaxError,
    evaluate,
    load_rule_file,
    parse_rule,
)
from sih.rules.geometry import BoundingBox, Point, Vector
from sih.rules.ops import OPS, avg_in_window, bbox, centroid, confidence, count_in_window, is_visible, persisted_for, speed
from sih.rules.types import (
    Check,
    Comparison,
    Condition,
    Emit,
    EmittedEvent,
    Literal,
    Logic,
    Mark,
    Not,
    OnExit,
    OpCall,
    OpResult,
    RecordResult,
    Rule,
    RuleRunResult,
    StepResult,
    TimeExpr,
    TimeRef,
    ValueExpr,
    VarRef,
)

__all__ = [
    "BoundingBox",
    "Check",
    "Comparison",
    "Condition",
    "Emit",
    "EmittedEvent",
    "Literal",
    "Logic",
    "Mark",
    "Not",
    "OPS",
    "OnExit",
    "OpCall",
    "OpResult",
    "Point",
    "RecordResult",
    "Rule",
    "RuleRegistry",
    "RuleRunResult",
    "RuleSyntaxError",
    "StepResult",
    "TimeExpr",
    "TimeRef",
    "ValueExpr",
    "VarRef",
    "Vector",
    "avg_in_window",
    "bbox",
    "centroid",
    "confidence",
    "count_in_window",
    "evaluate",
    "is_visible",
    "load_rule_file",
    "parse_rule",
    "persisted_for",
    "speed",
]