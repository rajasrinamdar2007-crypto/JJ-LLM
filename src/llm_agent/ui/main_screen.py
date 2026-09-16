from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual.app import Screen
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import DataTable, Footer, Header, Input, Label, RichLog

from llm_agent.rules_store import RulesStore


class MainScreen(Screen[None]):
    BINDINGS: ClassVar = [
        Binding("r", "refresh", "Refresh"),
        Binding("n", "focus_input", "New rule", show=True),
    ]

    def __init__(self, rules_dir: Path) -> None:
        super().__init__()
        self.rules_dir = rules_dir
        self.store = RulesStore(rules_dir)

    def compose(self):
        self._rules_table = DataTable(id="rules-table", cursor_type="row")
        self._rules_table.add_columns("Name", "ID", "Created")

        self._chat_log = RichLog(id="chat-log", markup=True, wrap=True)

        yield Header()
        with Container(id="body"):
            with Container(id="rules-panel"):
                yield Label("Rules", id="rules-title")
                yield self._rules_table
            with Container(id="chat-column"):
                yield self._chat_log
                with Container(id="composer"):
                    yield Input(placeholder="Ask for a rule…", id="chat-input")
        yield Footer()

    def on_mount(self) -> None:
        self._populate_rules()
        self.call_later(
            self._chat_log.write,
            Text.from_markup("rulebox — type a query to create a rule"),
        )
        self.query_one("#chat-input", Input).focus()

    def _populate_rules(self) -> None:
        self._rules_table.clear()
        rules = self.store.load()
        if not rules:
            self._chat_log.write(Text.from_markup("No rules in rules/ yet"))
        for rule in rules:
            self._rules_table.add_row(rule.name, rule.id, rule.created_at[:10])

    def action_refresh(self) -> None:
        self._populate_rules()
        self._chat_log.write(Text.from_markup("refreshed"))

    def action_focus_input(self) -> None:
        self.query_one("#chat-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
        event.input.value = ""

        self._chat_log.write(Text.from_markup(f"You:  {query}"))

        rule = self.store.create(name=query[:60], query=query)
        self._rules_table.add_row(rule.name, rule.id, rule.created_at[:10])

        self._chat_log.write(
            Text.from_markup("Agent:  (rule created — awaiting LLM integration)")
        )
