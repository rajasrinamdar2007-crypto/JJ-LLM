"""Rolling-window framebuffer of recent frames' entity detections.

This is the shared, globally importable state that the rule engine evaluates
rules against.  The input layer runs the detection model on each realtime
frame, converts the raw model output into :class:`Entity` objects, and pushes
one :class:`FrameEntry` per frame into the module-level singleton below.

Rules never receive a framebuffer reference through their arguments — they
import this module and read from the singleton directly.

Thread-safety note: the buffer is written by the input/detection side and read
concurrently by the async rule-evaluation workers, so all access is guarded by
a lock.
"""

from __future__ import annotations

import threading
from collections import deque

from sih.common.types import FrameEntry

#: Default maximum number of frame entries kept in the window.  Sized in frame
#: counts.  A seconds-based window (``max_seconds``) is a possible future
#: config option; for now the size is a pure frame-count cap.
DEFAULT_MAX_FRAMES = 60


class EntityFrameBuffer:
    """A bounded, thread-safe sliding window of :class:`FrameEntry` items."""

    def __init__(self, max_frames: int = DEFAULT_MAX_FRAMES) -> None:
        self.max_frames: int = max_frames
        self._entries: deque[FrameEntry] = deque(maxlen=max_frames)
        self._lock: threading.Lock = threading.Lock()

    def configure(self, max_frames: int) -> None:
        """(Re)size the window.  Called once at pipeline startup."""
        with self._lock:
            self.max_frames = max_frames
            self._entries = deque(self._entries, maxlen=max_frames)

    def add(self, entry: FrameEntry) -> None:
        """Append a frame entry, dropping the oldest when the window is full."""
        with self._lock:
            self._entries.append(entry)

    def snapshot(self) -> list[FrameEntry]:
        """Return a copy of the current window (oldest → newest)."""
        with self._lock:
            return list(self._entries)

    def latest(self) -> FrameEntry | None:
        """Return the most recent frame entry, or None when empty."""
        with self._lock:
            return self._entries[-1] if self._entries else None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


#: Module-level singleton shared by input (writer) and rules (reader).
framebuffer = EntityFrameBuffer()

__all__ = ["EntityFrameBuffer", "framebuffer", "DEFAULT_MAX_FRAMES"]