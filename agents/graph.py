from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agents import job_search_agent, matching_agent, resume_agent, skill_gap_agent
from agents.state import GraphState


def _has_jobs(state: GraphState) -> str:
    return "matching" if state.get("jobs") else "no_jobs"


def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("resume", resume_agent.run)
    graph.add_node("job_search", job_search_agent.run)
    graph.add_node("matching", matching_agent.run)
    graph.add_node("skill_gap", skill_gap_agent.run)

    graph.set_entry_point("resume")
    graph.add_edge("resume", "job_search")
    graph.add_conditional_edges("job_search", _has_jobs, {"matching": "matching", "no_jobs": END})
    graph.add_edge("matching", "skill_gap")
    graph.add_edge("skill_gap", END)

    # In-memory checkpoints: each pipeline run completes within one request, so nothing needs to persist.
    return graph.compile(checkpointer=MemorySaver())
