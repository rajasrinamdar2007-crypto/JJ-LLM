"""Pure, picklable data models shared across the pipeline.

These dataclasses hold only plain Python data (numbers, strings, and numpy
arrays) so they can safely cross thread or process boundaries. No files,
locks, or other non-picklable objects live here.

Units are explicit in the field names to avoid silent unit bugs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import numpy as np

#: Source of a GPS fix: raw GGA, raw RMC, or merged from both.
GPS_SOURCE_GGA = "gga"
GPS_SOURCE_RMC = "rmc"
GPS_SOURCE_MERGED = "merged"


@dataclass
class GPSFix:
    """A single GPS measurement at a point in time."""

    ts: float
    lat: float
    lon: float
    #: Altitude in metres above sea level.
    altitude_m: float
    #: Ground speed in metres per second.
    speed_mps: float
    #: Heading in degrees (0-360, clockwise from north).
    heading_deg: float
    #: 0 = no fix, 1 = GPS fix, 2 = DGPS fix, ...
    fix_quality: int
    num_sats: int
    #: One of gga / rmc / merged.
    source: str

    def latlon(self) -> tuple[float, float]:
        """Return (lat, lon) as a handy tuple."""
        return (self.lat, self.lon)


@dataclass
class Frame:
    """A single video frame."""

    id: int
    #: Timestamp in seconds (unix or relative; must be comparable with GPS).
    ts: float
    width: int
    height: int
    #: The BGR pixel data as a numpy array.
    frame_data: np.ndarray
    #: Optional path to the file this frame came from (set when saved).
    file_path: str | None = None


@dataclass
class ModelResult:
    """Result from a single model inference."""

    model_name: str
    #: Bounding boxes in [x1, y1, x2, y2] format (pixel coordinates)
    boxes: np.ndarray | None = None
    #: Class IDs for each detection
    class_ids: np.ndarray | None = None
    #: Confidence scores for each detection
    scores: np.ndarray | None = None
    #: Class names mapping (optional)
    class_names: dict[int, str] | None = None
    #: Inference time in milliseconds
    inference_ms: float = 0.0


@dataclass
class FrameWithGPS:
    """A frame paired with its interpolated GPS fix for processing."""

    frame: Frame
    gps: GPSFix
    #: Results from all models run on this frame
    model_results: list[ModelResult] = field(default_factory=list)


@dataclass
class SyncedSample:
    """A frame plus the GPS fix that matches it in time."""

    frame: Frame
    gps: GPSFix


@dataclass
class Entity:
    """A single tracked physical object detected in a frame.

    Entities are the atomic unit that rules operate on.  They carry the
    tracker-assigned persistent ID so that the same pothole / crack keeps
    its identity across frames.
    """

    #: Stable tracker ID (BoT-SORT or equivalent).
    tracker_id: int
    #: Model class id (int) or class name string — the caller decides.
    class_id: int
    #: Human-readable class label (e.g. "pothole", "crack").
    class_name: str
    #: Bounding box [x1, y1, x2, y2] in pixel coordinates.
    bbox: list[float]
    #: Detection confidence in [0, 1].
    confidence: float
    #: Frame timestamp (seconds) — preserved for temporal primitives.
    ts: float
    #: Frame ID this entity was observed in.
    frame_id: int


@dataclass
class FrameEntry:
    """One frame's worth of entity detections, stored in the rolling buffer.

    ``EntityFrameBuffer`` holds a deque of these.  ``FrameEntry`` is the unit
    of time that temporal primitives (``count_over``, ``mean_over``, …) slice
    over.
    """

    frame_id: int
    ts: float
    gps_lat: float
    gps_lon: float
    entities: list[Entity]
    #: Original frame dimensions (populated when coming from the pipeline).
    width: int = 0
    height: int = 0


@dataclass
class AnalyticsEvent:
    """A detection / analytic result to persist and later sync.

    Note there is no image blob here; we only store a path to an image file
    on disk (see ``image_path``).
    """

    event_uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: float = 0.0
    event_type: str = ""
    confidence: float = 0.0
    payload_json: str = "{}"
    lat: float = 0.0
    lon: float = 0.0
    image_path: str | None = None
