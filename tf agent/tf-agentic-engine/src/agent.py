from typing import Any
from langgraph.graph import StateGraph, END

from src.state import GraphState
from src.nodes import (
    generate_network_node,
    generate_security_node,
    generate_compute_node,
    generate_data_node,
    validation_node_func,
    routing_decision_router,
    pre_flight_validation_node,
    generate_graph_node,
)


def build_workflow() -> Any:
    workflow = StateGraph(GraphState)

    # Register nodes
    workflow.add_node("generate_network", generate_network_node)
    workflow.add_node("generate_security", generate_security_node)
    workflow.add_node("generate_compute", generate_compute_node)
    workflow.add_node("generate_data", generate_data_node)
    workflow.add_node("pre_flight_validation", pre_flight_validation_node)
    workflow.add_node("validate_code", validation_node_func)
    workflow.add_node("generate_graph", generate_graph_node)

    workflow.set_entry_point("generate_network")

    # Linear edges
    workflow.add_edge("generate_network", "generate_security")
    workflow.add_edge("generate_security", "generate_compute")
    workflow.add_edge("generate_compute", "generate_data")
    workflow.add_edge("generate_data", "pre_flight_validation")
    workflow.add_edge("pre_flight_validation", "validate_code")
    workflow.add_edge("generate_graph", END)

    # Conditional router after validation (map fix_<phase> -> generate_<phase>)
    workflow.add_conditional_edges(
        "validate_code",
        routing_decision_router,
        {
            "fix_network": "generate_network",
            "fix_security": "generate_security",
            "fix_compute": "generate_compute",
            "fix_data": "generate_data",
            "generate_graph": "generate_graph",
            "complete": END,
        },
    )

    return workflow.compile()


# Export compiled app
app = build_workflow()
