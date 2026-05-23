from engine.state import GraphState
from engine.llm_client import call_cloud_llm
from engine.prompts import NETWORK_PROMPT
from engine.validator import parse_and_write_files


def generate_network_node(state: GraphState) -> dict:
    print("[Node] Generating Network Configuration...")
    hcl = call_cloud_llm(NETWORK_PROMPT, {"aws_input_data": state.get("aws_input_data")})
    parse_and_write_files(hcl, phase_filename="network.tf")
    return {"network_hcl": hcl, "current_phase": "network"}
