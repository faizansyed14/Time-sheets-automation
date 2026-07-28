"""Agentic extraction orchestrator.

    Orchestrator([...agents]).run(AgentContext)

ONE pipeline, used by every entry point (Extract Email, Upload, chat store):
a two-pass vision read over the whole conversation/submission, then a
deterministic tail.

`build_thread_pipeline()`:

    1 Thread         whole conversation/submission → two-pass JSON     (LLM)
    2 Employee       resolve identity against the HR master           (det.)
    3 Conversation   merge 1–15 / 16–30 / weekly partials into a month(det.)
    4 Duplicate      repeat submissions, already-filed months         (det.)
    5 Decision       auto-accept vs review, then file                 (det.)

There is no fallback pipeline: a vision model is mandatory (see
thread_extract.require_vision_configured).
"""
from app.services.orchestrator.agents import build_thread_pipeline
from app.services.orchestrator.base import Agent, AgentContext, AgentInfo
from app.services.orchestrator.orchestrator import Orchestrator

__all__ = ["Agent", "AgentContext", "AgentInfo", "Orchestrator", "build_thread_pipeline"]
