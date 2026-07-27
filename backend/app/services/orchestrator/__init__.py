"""Agentic extraction orchestrator.

    Orchestrator([...agents]).run(AgentContext)

Two line-ups share one deterministic tail.

`build_thread_pipeline()` — Extract Email. ONE model call carries the whole
conversation (every body, every attachment, images included):

    1 Thread         whole conversation → one JSON document           (LLM)
    2 Approval       fallback only; the thread call normally answers this
    3 Employee       resolve identity against the HR master           (det.)
    4 Conversation   merge 1–15 / 16–30 / weekly partials into a month(det.)
    5 Duplicate      repeat submissions, already-filed months, and the
                     thread call's cross-sheet conflicts              (det.)
    6 Validation     business rules                                   (det.)
    7 Decision       auto-accept vs review, then file                 (det.)

`build_pipeline()` — Upload / Manual entry, where there is no conversation to
read: unpack → route → per-sheet vision → same tail.
"""
from app.services.orchestrator.agents import build_pipeline, build_thread_pipeline
from app.services.orchestrator.base import Agent, AgentContext, AgentInfo
from app.services.orchestrator.orchestrator import Orchestrator

__all__ = ["Agent", "AgentContext", "AgentInfo", "Orchestrator",
           "build_pipeline", "build_thread_pipeline"]
