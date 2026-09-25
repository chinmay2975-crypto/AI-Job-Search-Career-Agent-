"""Deterministic planner for the autonomous apply flow.

Every specialist hands control back here; routing is plain Python over state flags (never an
LLM decision): resume -> discovery -> matching -> apply (one job per step) -> end.
"""

from agents.state import AutonomousState


def next_step(state: AutonomousState) -> str:
    if not state.get("candidate"):
        return "resume"
    if not state.get("discovery_done"):
        return "discovery"
    if not state.get("discovered_jobs"):
        return "end"
    if not state.get("matching_done"):
        return "matching"
    if state.get("apply_queue"):
        return "apply"
    return "end"


def run(state: AutonomousState) -> AutonomousState:
    return state
