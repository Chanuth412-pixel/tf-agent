from typing import TypedDict, Any, List
from langgraph.graph import StateGraph, END

import os
import json
import sys

# Use absolute imports referencing the engine package
from engine.llm_client import call_cloud_llm
from engine.prompts import NETWORK_PROMPT, SECURITY_PROMPT, COMPUTE_PROMPT, DATA_PROMPT
from engine.validator import parse_and_write_files, execute_terraform_validation


# Allow a CLI flag to skip running terraform for fast dry-runs
SKIP_VALIDATE = ("--skip-validate" in sys.argv) or ("--dry-run" in sys.argv) or (os.environ.get("SKIP_VALIDATE") == "1")


# Define the GraphState with independent HCL chunks
class GraphState(TypedDict):
    aws_input_data: dict
    retry_count: int
    max_retries: int
    current_phase: str
    network_hcl: str
    security_hcl: str
    compute_hcl: str
    data_hcl: str
    error_logs: List[str]
    is_valid: bool


def create_initial_state(raw_json: dict) -> GraphState:
    return GraphState(
        aws_input_data=raw_json,
        retry_count=0,
        max_retries=3,
        current_phase="network",
        network_hcl="",
        security_hcl="",
        compute_hcl="",
        data_hcl="",
        error_logs=[],
        is_valid=False,
    )


# -------------------------
# Mock node implementations
# -------------------------
def network_node_func(state: GraphState) -> dict:
    print("[Node] generate_network")
    # Ask the LLM (or fallback) for network HCL
    hcl = call_cloud_llm(NETWORK_PROMPT, {"aws_input_data": state.get("aws_input_data")})

    parse_and_write_files(hcl, phase_filename="network.tf")

    return {"network_hcl": hcl, "current_phase": "network"}


def security_node_func(state: GraphState) -> dict:
    print("[Node] generate_security")
    hcl = call_cloud_llm(SECURITY_PROMPT, {"aws_input_data": state.get("aws_input_data"), "network_context": state.get("network_hcl")})

    parse_and_write_files(hcl, phase_filename="security.tf")
    return {"security_hcl": hcl, "current_phase": "security"}


def compute_node_func(state: GraphState) -> dict:
    print("[Node] generate_compute")
    hcl = call_cloud_llm(COMPUTE_PROMPT, {"aws_input_data": state.get("aws_input_data"), "network_context": state.get("network_hcl"), "security_context": state.get("security_hcl")})

    parse_and_write_files(hcl, phase_filename="compute.tf")
    return {"compute_hcl": hcl, "current_phase": "compute"}


def data_node_func(state: GraphState) -> dict:
    print("[Node] generate_data")
    hcl = call_cloud_llm(DATA_PROMPT, {"aws_input_data": state.get("aws_input_data"), "network_context": state.get("network_hcl"), "security_context": state.get("security_hcl")})

    parse_and_write_files(hcl, phase_filename="data.tf")
    return {"data_hcl": hcl, "current_phase": "data"}


def validation_node_func(state: GraphState) -> dict:
    print("[Node] validate_code")
    # Optional dry-run: bypass CLI validation for prototyping
    if SKIP_VALIDATE:
        print("[Validator] SKIP_VALIDATE enabled; forcing success (dry-run).")
        return {"is_valid": True}

    # Run terraform validation across the workspace
    success, output = execute_terraform_validation()

    if success:
        return {"is_valid": True}
    else:
        # attach error and route back to the last phase, increment retry counter
        state_errors = state.get("error_logs", [])
        state_errors.append(output)
        retry = state.get("retry_count", 0) + 1
        print(f"[Validator] Validation failed. Incrementing retry_count -> {retry}")
        return {"is_valid": False, "error_logs": state_errors, "retry_count": retry}



# -------------------------
# Build the StateGraph
# -------------------------

workflow = StateGraph(GraphState)

workflow.add_node("generate_network", network_node_func)
workflow.add_node("generate_security", security_node_func)
workflow.add_node("generate_compute", compute_node_func)
workflow.add_node("generate_data", data_node_func)
workflow.add_node("validate_code", validation_node_func)

workflow.set_entry_point("generate_network")

workflow.add_edge("generate_network", "generate_security")
workflow.add_edge("generate_security", "generate_compute")
workflow.add_edge("generate_compute", "generate_data")
workflow.add_edge("generate_data", "validate_code")


def route_after_validation(state: GraphState) -> str:
    print("[Router] routing after validation")
    if state.get("is_valid"):
        return "end"

    # Stop if retries exceeded
    if state.get("retry_count", 0) >= state.get("max_retries", 3):
        return "end"

    # Route back to the phase that was most recently produced
    phase = state.get("current_phase", "network")
    mapping = {
        "network": "generate_network",
        "security": "generate_security",
        "compute": "generate_compute",
        "data": "generate_data",
    }
    return mapping.get(phase, "generate_network")


workflow.add_conditional_edges(
    "validate_code",
    route_after_validation,
    {"end": END, "generate_network": "generate_network", "generate_security": "generate_security", "generate_compute": "generate_compute", "generate_data": "generate_data"},
)

app = workflow.compile()


if __name__ == "__main__":
    # Dry run using the mock file
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    mock_path = os.path.join(repo_root, "scanner", "mock_infra.json")
    if not os.path.exists(mock_path):
        print(f"Missing mock file: {mock_path}")
        exit(1)

    with open(mock_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    initial = create_initial_state(raw)
    print("Starting LangGraph execution...")
    final = app.invoke(initial)
    print("Final state:")
    print(final)

