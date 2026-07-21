"""Runs the agent pipeline and streams each agent's progress to the UI.

Every agent gets an `agent` progress frame pair (spin → ok/warn/skip) carrying
its machine name + label, so the front-end can render a live agent checklist
("which agent is working right now"). An agent that raises does NOT kill the
run: the failure is recorded as a note and the pipeline continues, because a
partial result a human can fix beats no result at all.
"""
from __future__ import annotations

import time

from app.services.extract_email.progress import emit
from app.services.orchestrator.base import Agent, AgentContext


class Orchestrator:
    def __init__(self, agents: list[Agent]) -> None:
        self.agents = agents

    def manifest(self) -> list[dict]:
        """The agent line-up — sent to the UI up-front so it can render the
        full checklist immediately, with each entry filling in as it runs."""
        return [{"name": a.info.name, "label": a.info.label,
                 "description": a.info.description, "uses_llm": a.info.uses_llm}
                for a in self.agents]

    async def run(self, ctx: AgentContext) -> AgentContext:
        emit("plan", "ok", "Agent pipeline ready.", agents=self.manifest())

        for agent in self.agents:
            info = agent.info
            if ctx.aborted:
                emit("agent", "skip", f"{info.label} — skipped ({ctx.aborted})",
                     agent=info.name, label=info.label)
                continue

            reason = agent.skip_reason(ctx)
            if reason:
                emit("agent", "skip", f"{info.label} — skipped ({reason})",
                     agent=info.name, label=info.label)
                continue

            emit("agent", "spin", f"{info.label} — {info.description}",
                 agent=info.name, label=info.label, uses_llm=info.uses_llm)
            started = time.monotonic()
            try:
                summary = await agent.run(ctx)
                took = int((time.monotonic() - started) * 1000)
                emit("agent", "ok", f"{info.label} — {summary}",
                     agent=info.name, label=info.label, took_ms=took)
            except Exception as exc:                     # keep going, record it
                took = int((time.monotonic() - started) * 1000)
                note = f"{info.label} failed: {str(exc)[:160]}"
                ctx.notes.append(note)
                emit("agent", "warn", note,
                     agent=info.name, label=info.label, took_ms=took)
        return ctx
