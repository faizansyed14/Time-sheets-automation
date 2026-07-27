"""Agent framework — the contract every extraction agent implements.

DESIGN NOTE (why not "8 LLM agents"):
An agent here is a named, independently-observable unit of work with ONE
responsibility — not necessarily an LLM call. Employee resolution (fuzzy
matching), validation (business rules), duplicate detection, period merging
and the final decision are deterministic problems where code is *more*
accurate than a model and costs nothing. Only the Vision and Approval agents
call the LLM. Every agent reports start/finish to the UI either way, so the
operator sees exactly which agent is working.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class AgentContext:
    """Shared state threaded through the agent pipeline. Each agent reads what
    it needs and writes its own output — no agent reaches into another's."""

    db: AsyncSession
    source_kind: str                       # "email" | "upload"
    source_id: str
    source: Any                            # EmailMessage | SourceCtx
    raw_bytes: bytes = b""
    raw_name: str = ""
    content_type: str = ""
    prior_source: Any = None               # previous message in the thread
    # Conversation id for Extract Email — the dedupe key for review items, so
    # a thread re-extracted after a new reply updates rather than duplicates.
    thread_key: str | None = None

    # ---- progressively filled by the agents ----
    units: list = field(default_factory=list)          # EmailAgent/AttachmentAgent
    sheets: list[dict] = field(default_factory=list)    # VisionAgent
    run_meta: dict = field(default_factory=dict)        # VisionAgent
    approval: dict = field(default_factory=dict)        # ApprovalAgent
    groups: list[dict] = field(default_factory=list)    # EmployeeAgent
    staged: list = field(default_factory=list)          # DecisionAgent
    notes: list[str] = field(default_factory=list)      # any agent, surfaced to UI
    aborted: str | None = None                          # set to stop the pipeline
    message: str = ""                                   # final human message
    # Extract Email: every message in the conversation as (label, .eml bytes),
    # oldest first — the ThreadAgent sends them all in one call.
    thread_messages: list = field(default_factory=list)
    # Cross-sheet conflicts the thread call reported (duplicate full months,
    # complementary partials, re-sends).
    conflicts: list[dict] = field(default_factory=list)

    def abort(self, reason: str) -> None:
        self.aborted = reason


@dataclass
class AgentInfo:
    name: str          # machine id, e.g. "vision"
    label: str         # UI label, e.g. "Vision Agent"
    description: str
    uses_llm: bool = False


class Agent(ABC):
    """One responsibility, observable start→finish."""

    info: AgentInfo

    def skip_reason(self, ctx: AgentContext) -> str | None:
        """Return a reason to skip this agent for this run (else None)."""
        return None

    @abstractmethod
    async def run(self, ctx: AgentContext) -> str:
        """Do the work; return a one-line summary for the activity feed."""
        ...
