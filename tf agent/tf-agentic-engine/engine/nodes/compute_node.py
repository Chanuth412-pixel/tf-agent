from engine.state import GraphState
from engine.llm_client import call_cloud_llm
from engine.prompts import COMPUTE_PROMPT
from engine.validator import parse_and_write_files


def generate_compute_node(state: GraphState) -> dict:
    print("[Node] Generating Compute Configuration...")
    hcl = call_cloud_llm(
        COMPUTE_PROMPT,
        {
            "aws_input_data": state.get("aws_input_data"),
            "network_context": state.get("network_hcl"),
            "security_context": state.get("security_hcl"),
        },
    )
    parse_and_write_files(hcl, phase_filename="compute.tf")
    return {"compute_hcl": hcl, "current_phase": "compute"}
