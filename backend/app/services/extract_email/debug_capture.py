"""Debug capture for the extraction pipeline — records the raw prompt text +
response JSON for every Pass 1/Pass 2 call, and full-resolution copies of
whatever gets dropped as noise/size/duplicate, so a run can be inspected
after the fact via /admin/debug instead of only live through the SSE stream.

Mirrors progress.py's contextvar pattern exactly: a `DebugCapture` is put on
a context variable for the duration of one run; pipeline code calls the
module-level `record_pass1()` / `record_pass2()` / `record_dropped()`
helpers, cheap no-ops when nothing is capturing, so the normal path (no
capture started) is completely unaffected.

Unlike progress.py this module does not persist anything itself — it only
accumulates in memory. The caller (ThreadAgent, which already has a DB
session) reads the finished `DebugCapture` back and writes the
ExtractionDebugRun row itself, and is responsible for saving full-resolution
dropped-item bytes via `save_dropped_image` (this module has no storage
dependency of its own, kept a pure in-memory collector like progress.py)."""
from __future__ import annotations

import contextvars
import uuid
from dataclasses import dataclass, field


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class DebugCapture:
    # Generated up front (not by the DB on insert) so dropped-item images can
    # be saved to a per-run storage folder keyed by this id WHILE the run is
    # still in progress — the ExtractionDebugRun row is only written at the
    # end, with this same id, once the caller has everything to persist.
    run_id: str = field(default_factory=_new_id)
    pass1_calls: list[dict] = field(default_factory=list)
    pass2_calls: list[dict] = field(default_factory=list)
    dropped_items: list[dict] = field(default_factory=list)

    def record_pass1(self, **kwargs) -> None:
        self.pass1_calls.append(kwargs)

    def record_pass2(self, **kwargs) -> None:
        self.pass2_calls.append(kwargs)

    def record_dropped(self, **kwargs) -> None:
        self.dropped_items.append(kwargs)


_CAPTURE: contextvars.ContextVar[DebugCapture | None] = contextvars.ContextVar(
    "extract_debug_capture", default=None)


def set_capture(cap: DebugCapture | None):
    return _CAPTURE.set(cap)


def reset_capture(token) -> None:
    _CAPTURE.reset(token)


def get_capture() -> DebugCapture | None:
    return _CAPTURE.get()


def record_pass1(**kwargs) -> None:
    cap = _CAPTURE.get()
    if cap is not None:
        cap.record_pass1(**kwargs)


def record_pass2(**kwargs) -> None:
    cap = _CAPTURE.get()
    if cap is not None:
        cap.record_pass2(**kwargs)


def record_dropped(**kwargs) -> None:
    cap = _CAPTURE.get()
    if cap is not None:
        cap.record_dropped(**kwargs)
