"""Thread-safe circular frame buffer for real-time access.

Stores one :class:`FrameWithGPS` triplet ``(Frame, GPSFix, model_results)`` per
processed frame.  Only frames that have actually been analysed (YOLO has run)
are pushed; unprocessed/backlog-dropped frames never enter the deque.
"""

from __future__ import annotations

import threading
from collections import deque

from sih.common.types import FrameWithGPS


class FrameBuffer:
    """Thread-safe buffer storing recent frames with GPS for real-time access."""

    def __init__(self, max_size: int = 100) -> None:
        self.max_size: int = max_size
        self._buffer: deque[FrameWithGPS] = deque(maxlen=max_size)
        self._lock: threading.Lock = threading.Lock()

    def add(self, frame_with_gps: FrameWithGPS) -> None:
        """Add a processed frame to the buffer."""
        with self._lock:
            self._buffer.append(frame_with_gps)

    def get_latest(self, count: int | None = None) -> list[FrameWithGPS]:
        """Get the most recent frames (up to count)."""
        with self._lock:
            if count is None:
                return list(self._buffer)
            return list(self._buffer)[-count:]

    def get_by_id(self, frame_id: int) -> FrameWithGPS | None:
        """Get a specific frame by ID."""
        with self._lock:
            for fwg in reversed(self._buffer):
                if fwg.frame.id == frame_id:
                    return fwg
        return None

    def clear(self) -> None:
        """Clear the buffer."""
        with self._lock:
            self._buffer.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)
