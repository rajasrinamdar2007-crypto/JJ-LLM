"""Input layer: the shared entity framebuffer.

The physical ingestion path (camera, gps, sequencer) is imported directly by
``pipeline.py`` and produces raw frames; the detection + tracking pass happens
once per frame inside ``processing.road_damage_task``, which writes its
tracker output into ``framebuffer``.  This package's role in the restructured
pipeline is therefore a single one:

1. ``framebuffer`` — maintain a rolling window of ``FrameEntry`` items so the
   rule engine can evaluate temporal primitives without receiving a reference
   through every function argument.
"""

from sih.input.framebuffer import EntityFrameBuffer, framebuffer

__all__ = [
    "EntityFrameBuffer",
    "framebuffer",
]