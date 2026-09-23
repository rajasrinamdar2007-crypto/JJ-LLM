from __future__ import annotations

import time
from pathlib import Path
from typing import ClassVar

from textual.app import App
from textual.binding import Binding

from llm_agent.ui.main_screen import MainScreen

RULES_DIR = Path(__file__).resolve().parent.parent.parent / "LM-rules"


class App(App[None]):
    BINDINGS: ClassVar = [Binding("ctrl+c", "ctrl_c", priority=True)]
    CSS_PATH = str(Path(__file__).resolve().parent / "ui" / "styles.css")
    TITLE = "rulebox"
    _last_ctrl_c: float = 0.0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rules_dir = RULES_DIR

    def on_mount(self) -> None:
        self.push_screen(MainScreen(rules_dir=self.rules_dir))

    def action_ctrl_c(self) -> None:
        now = time.monotonic()
        if now - self._last_ctrl_c < 1.0:
            self.exit()
        else:
            self._last_ctrl_c = now
            self.notify(
                "Press Ctrl+C again to quit",
                title="Quit",
                severity="information",
            )
