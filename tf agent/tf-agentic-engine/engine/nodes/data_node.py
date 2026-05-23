from engine.state import GraphState
from engine.llm_client import call_cloud_llm
from engine.prompts import DATA_PROMPT
from engine.validator import parse_and_write_files


def generate_data_node(state: GraphState) -> dict:
    print("[Node] Generating Data Configuration...")
    hcl = call_cloud_llm(
        DATA_PROMPT,
        {
            "aws_input_data": state.get("aws_input_data"),
            "network_context": state.get("network_hcl"),
            "security_context": state.get("security_hcl"),
        },
    )
    parse_and_write_files(hcl, phase_filename="data.tf")
    return {"data_hcl": hcl, "current_phase": "data"}
