from langgraph.graph import END, StateGraph

from agents import apply_agent, autonomous_matching_agent, candidate_intake_agent, discovery_agent, planner_agent
from agents.state import AutonomousState

_SPECIALISTS = {
    "resume": candidate_intake_agent.run,
    "discovery": discovery_agent.run,
    "matching": autonomous_matching_agent.run,
    "apply": apply_agent.run,
}


def build_autonomous_graph():
    graph = StateGraph(AutonomousState)
    graph.add_node("planner", planner_agent.run)
    for name, node in _SPECIALISTS.items():
        graph.add_node(name, node)
        graph.add_edge(name, "planner")

    graph.set_entry_point("planner")
    graph.add_conditional_edges("planner", planner_agent.next_step, {**{n: n for n in _SPECIALISTS}, "end": END})
    return graph.compile()


# Upper bound on postings one source can contribute: a page of search results plus expanded boards.
_MAX_POSTINGS_PER_SOURCE = 40


def recursion_limit(max_jobs: int, source_count: int = 1) -> int:
    """Each queued posting costs two graph steps (planner -> apply). Auto-submissions are capped by
    max_jobs, but drafts and links for you to apply yourself aren't, so bound by what discovery can find."""
    return 2 * (max_jobs + source_count * _MAX_POSTINGS_PER_SOURCE) + 20
