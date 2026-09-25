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


def recursion_limit(max_jobs: int) -> int:
    """Each applied job costs two graph steps (planner -> apply); leave headroom for the rest."""
    return 2 * max_jobs + 20
