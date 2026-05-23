from typing import TypedDict, List, Dict


class GraphState(TypedDict):
    aws_input_data: Dict
    retry_count: int
    max_retries: int
    current_phase: str  # "network", "security", "compute", "data"
    network_hcl: str
    security_hcl: str
    compute_hcl: str
    data_hcl: str
    error_logs: List[str]
    is_valid: bool


def create_initial_state(raw_json: Dict) -> GraphState:
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
