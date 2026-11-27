"""An agent that crashes must never turn into 'checked - no timesheet here'."""
from app.services.orchestrator.base import AgentContext, AgentInfo
from app.services.orchestrator.orchestrator import Orchestrator


class _Boom:
    info = AgentInfo(name="boom", label="Boom Agent", description="crashes")

    def skip_reason(self, ctx):
        return None

    async def run(self, ctx):
        raise IndexError("list index out of range")


async def test_a_crashing_agent_marks_the_run_incomplete():
    ctx = AgentContext(db=None, source_kind="email", source_id="x", source=None)
    await Orchestrator([_Boom()]).run(ctx)
    assert ctx.run_meta.get("incomplete") is True
    assert any("Boom Agent failed" in e for e in ctx.run_meta.get("errors", []))
