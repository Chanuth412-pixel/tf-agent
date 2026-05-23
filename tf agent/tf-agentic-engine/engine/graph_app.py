from typing import Any
import os
import json

from langgraph.graph import StateGraph, END

from engine.state import GraphState, create_initial_state
from engine.router import routing_decision_router

# Import modular nodes
from engine.nodes.network_node import generate_network_node
from engine.nodes.security_node import generate_security_node
from engine.nodes.compute_node import generate_compute_node
from engine.nodes.data_node import generate_data_node
from engine.nodes.validation_node import validation_node_func


def build_workflow() -> Any:
    workflow = StateGraph(GraphState)

    # Register nodes
    workflow.add_node("generate_network", generate_network_node)
    workflow.add_node("generate_security", generate_security_node)
    workflow.add_node("generate_compute", generate_compute_node)
    workflow.add_node("generate_data", generate_data_node)
    workflow.add_node("validate_code", validation_node_func)

    workflow.set_entry_point("generate_network")

    # Linear edges
    workflow.add_edge("generate_network", "generate_security")
    workflow.add_edge("generate_security", "generate_compute")
    workflow.add_edge("generate_compute", "generate_data")
    workflow.add_edge("generate_data", "validate_code")

    # Conditional router after validation (map fix_<phase> -> generate_<phase>)
    workflow.add_conditional_edges(
        "validate_code",
        routing_decision_router,
        {
            "fix_network": "generate_network",
            "fix_security": "generate_security",
            "fix_compute": "generate_compute",
            "fix_data": "generate_data",
            "complete": END,
        },
    )

    return workflow.compile()


if __name__ == "__main__":
    # Load mock data
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    mock_path = os.path.join(repo_root, "scanner", "mock_infra.json")
    if not os.path.exists(mock_path):
        print(f"Missing mock file: {mock_path}")
        exit(1)

    with open(mock_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    initial = create_initial_state(raw)

    print("Initializing Modular LangGraph Engine...")
    app = build_workflow()
    final = app.invoke(initial)

    print("--- FINAL STATE ---")
    print(final)
