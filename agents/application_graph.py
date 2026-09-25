from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agents import apply_executor_agent, await_approval_agent, classify_ats_agent, cover_letter_agent
from agents.state import ApplicationState


def _route_after_classify(state: ApplicationState) -> str:
    if state.get("execution_strategy") == "blocked":
        return "blocked_end"
    if state.get("below_threshold"):
        return "below_threshold_end"
    return "draft"


def _route_after_draft(state: ApplicationState) -> str:
    # auto_submit (synthetic/greenhouse/lever) goes straight to the executor - DRY_RUN is the
    # safety net there, not a human gate. assisted_draft (workday/indeed)
    # always needs a human to look at the draft first, even though it can never auto-submit.
    return "apply" if state.get("execution_strategy") == "auto_submit" else "await_approval"


def _route_after_approval(state: ApplicationState) -> str:
    return "apply" if state.get("human_approved") else "rejected_end"


def build_application_graph():
    graph = StateGraph(ApplicationState)

    graph.add_node("classify_ats", classify_ats_agent.run)
    graph.add_node("draft_cover_letter", cover_letter_agent.run)
    graph.add_node("await_approval", await_approval_agent.run)
    graph.add_node("apply_executor", apply_executor_agent.run)

    graph.set_entry_point("classify_ats")
    graph.add_conditional_edges(
        "classify_ats",
        _route_after_classify,
        {"blocked_end": END, "below_threshold_end": END, "draft": "draft_cover_letter"},
    )
    graph.add_conditional_edges(
        "draft_cover_letter", _route_after_draft, {"apply": "apply_executor", "await_approval": "await_approval"}
    )
    graph.add_conditional_edges("await_approval", _route_after_approval, {"apply": "apply_executor", "rejected_end": END})
    graph.add_edge("apply_executor", END)

    # MemorySaver: durable enough for a single running process. The `applications` row in the
    # database (not this checkpoint) is the source of truth if the process restarts mid-approval.
    return graph.compile(checkpointer=MemorySaver())
