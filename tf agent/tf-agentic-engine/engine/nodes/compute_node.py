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
            "network_context": (
                "Available infrastructure references: vpc_id = aws_vpc.main.id, "
                "public_subnet_id = aws_subnet.public_1.id, private_subnet_id = aws_subnet.private_1.id. "
                "DO NOT declare these resource blocks again."
            ),
            "security_context": (
                "Available security references: security_group_id = aws_security_group.vpc_sg.id. "
                "DO NOT declare this block again."
            ),
        },
    )
    parse_and_write_files(hcl, phase_filename="compute.tf")
    return {"compute_hcl": hcl, "current_phase": "compute"}
