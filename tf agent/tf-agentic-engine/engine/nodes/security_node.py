from engine.state import GraphState
from engine.llm_client import call_cloud_llm
from engine.prompts import SECURITY_PROMPT
from engine.validator import parse_and_write_files


def generate_security_node(state: GraphState) -> dict:
    print("[Node] Generating Security Configuration...")
    hcl = call_cloud_llm(
        SECURITY_PROMPT,
        {
            "aws_input_data": state.get("aws_input_data"),
            "network_context": state.get("network_hcl"),
        },
    )
    parse_and_write_files(hcl, phase_filename="security.tf")
    return {"security_hcl": hcl, "current_phase": "security"}
