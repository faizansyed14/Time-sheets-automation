"""Agentic extraction orchestrator.

    Orchestrator([...agents]).run(AgentContext)

Nine specialised agents, each with one responsibility and each reporting its
own start/finish to the live UI:

    1 Email          unpack message + nested emails (deterministic)
    2 Attachment     route PDF/XLSX/DOCX/image, detect client template (det.)
    3 OCR / Vision   read every sheet with the model                  (LLM)
    4 Approval       find a manager sign-off anywhere in the thread
    5 Employee       resolve identity against the HR master           (det.)
    6 Conversation   merge 1–15 / 16–30 / weekly partials into a month(det.)
    7 Duplicate      repeat submissions + already-filed months        (det.)
    8 Validation     business rules                                   (det.)
    9 Decision       auto-accept vs review, then file                 (det.)
"""
from app.services.orchestrator.agents import build_pipeline
from app.services.orchestrator.base import Agent, AgentContext, AgentInfo
from app.services.orchestrator.orchestrator import Orchestrator

__all__ = ["Agent", "AgentContext", "AgentInfo", "Orchestrator", "build_pipeline"]
