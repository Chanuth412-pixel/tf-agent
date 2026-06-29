from typing import TypedDict, Dict, Any


class GraphState(TypedDict):
    # --- Deployment Configuration ---
    deployment_mode: str  # Valid values: "new", "import", "clone"
    user_prompt: str      # The requested architecture (used when mode is "new")
    target_vpc_id: str    # The ID of the target VPC if pre-existing
    dependency_map: Dict[str, Any] # Dependency map for pre-flight validation

    # --- Existing State ---
    aws_input_data: Dict[str, Any]
    retry_count: int
    max_retries: int
    current_phase: str
    network_hcl: str
    security_hcl: str
    compute_hcl: str
    data_hcl: str
    validation_results: str
    is_valid: bool


def create_initial_state(raw_json: Dict) -> GraphState:
    return GraphState(
        deployment_mode=raw_json.get("deployment_mode", "import"),
        user_prompt=raw_json.get("user_prompt", ""),
        target_vpc_id=raw_json.get("target_vpc_id", ""),
        dependency_map={},
        aws_input_data=raw_json.get("aws_input_data", {}),
        retry_count=0,
        max_retries=raw_json.get("max_retries", 3),
        current_phase=raw_json.get("current_phase", "network"),
        network_hcl="",
        security_hcl="",
        compute_hcl="",
        data_hcl="",
        validation_results="",
        is_valid=False,
    )
