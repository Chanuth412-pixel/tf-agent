import os
import sys
from src.state import GraphState, create_initial_state
from src.utils import (
    call_cloud_llm,
    parse_and_write_files,
    execute_terraform_validation,
    NETWORK_PROMPT,
    SECURITY_PROMPT,
    COMPUTE_PROMPT,
    DATA_PROMPT,
)


def routing_decision_router(state: GraphState) -> str:
    """Route based on validation outcome and retry limits.

    Returns one of: 'fix_network', 'fix_security', 'fix_compute', 'fix_data', or 'complete'
    """
    if state.get("is_valid"):
        return "complete"

    if state.get("retry_count", 0) >= state.get("max_retries", 3):
        print(
            f"[Router] Max retries ({state.get('max_retries')}) reached. Forcing exit."
        )
        return "complete"

    phase = state.get("current_phase", "network")
    return f"fix_{phase}"


def generate_network_node(state: GraphState) -> dict:
    print("[Node] Generating Network Configuration...")
    mode = state.get("deployment_mode")

    # Define mode-specific instructions
    mode_instructions = ""
    if mode == "new":
        mode_instructions = f"""
        MODE: NEW INFRASTRUCTURE
        Ignore any existing AWS data. Generate the HCL strictly based on this user request:
        {state.get('user_prompt')}
        """
    elif mode == "import":
                mode_instructions = """
                MODE: IMPORT EXISTING INFRASTRUCTURE
                Read the provided AWS input data. Generate the HCL resource blocks matching the data EXACTLY.
                Additionally, you MUST generate Terraform 1.5+ `import` blocks for every resource so Terraform can adopt them.
                Example syntax:
                import {{
                    to = aws_vpc.main
                    id = "vpc-12345"
                }}
                """
    elif mode == "clone":
        mode_instructions = """
        MODE: CLONE INFRASTRUCTURE
        Read the provided AWS input data to understand the architecture. 
        DO NOT hardcode the specific AWS IDs (e.g., vpc-12345) into the HCL. 
        Parameterize the code using standard variables so this exact architecture can be deployed as a brand new copy in a different region.
        """

    prompt = mode_instructions + "\n" + NETWORK_PROMPT

    hcl = call_cloud_llm(prompt, {"aws_input_data": state.get("aws_input_data"), "user_prompt": state.get("user_prompt")})
    parse_and_write_files(hcl, phase_filename="network.tf")
    return {"network_hcl": hcl, "current_phase": "network"}


def generate_security_node(state: GraphState) -> dict:
    print("[Node] Generating Security Configuration...")
    mode = state.get("deployment_mode")

    mode_instructions = ""
    if mode == "new":
        mode_instructions = f"""
        MODE: NEW INFRASTRUCTURE
        Ignore any existing AWS data. Generate the HCL strictly based on this user request:
        {state.get('user_prompt')}
        """
    elif mode == "import":
                mode_instructions = """
                MODE: IMPORT EXISTING INFRASTRUCTURE
                Read the provided AWS input data. Generate the HCL resource blocks matching the data EXACTLY.
                Additionally, you MUST generate Terraform 1.5+ `import` blocks for every resource so Terraform can adopt them.
                Example syntax:
                import {{
                    to = aws_vpc.main
                    id = "vpc-12345"
                }}
                """
    elif mode == "clone":
        mode_instructions = """
        MODE: CLONE INFRASTRUCTURE
        Read the provided AWS input data to understand the architecture. 
        DO NOT hardcode the specific AWS IDs (e.g., vpc-12345) into the HCL. 
        Parameterize the code using standard variables so this exact architecture can be deployed as a brand new copy in a different region.
        """

    prompt = mode_instructions + "\n" + SECURITY_PROMPT

    hcl = call_cloud_llm(
        prompt,
        {
            "aws_input_data": state.get("aws_input_data"),
            "user_prompt": state.get("user_prompt"),
            "network_context": (
                "An existing VPC named 'aws_vpc.main' and subnets 'aws_subnet.public_1' "
                "and 'aws_subnet.private_1' are already declared. DO NOT rewrite them."
            ),
        },
    )
    parse_and_write_files(hcl, phase_filename="security.tf")
    return {"security_hcl": hcl, "current_phase": "security"}


def generate_compute_node(state: GraphState) -> dict:
    print("[Node] Generating Compute Configuration...")
    mode = state.get("deployment_mode")

    mode_instructions = ""
    if mode == "new":
        mode_instructions = f"""
        MODE: NEW INFRASTRUCTURE
        Ignore any existing AWS data. Generate the HCL strictly based on this user request:
        {state.get('user_prompt')}
        """
    elif mode == "import":
                mode_instructions = """
                MODE: IMPORT EXISTING INFRASTRUCTURE
                Read the provided AWS input data. Generate the HCL resource blocks matching the data EXACTLY.
                Additionally, you MUST generate Terraform 1.5+ `import` blocks for every resource so Terraform can adopt them.
                Example syntax:
                import {{
                    to = aws_vpc.main
                    id = "vpc-12345"
                }}
                """
    elif mode == "clone":
        mode_instructions = """
        MODE: CLONE INFRASTRUCTURE
        Read the provided AWS input data to understand the architecture. 
        DO NOT hardcode the specific AWS IDs (e.g., vpc-12345) into the HCL. 
        Parameterize the code using standard variables so this exact architecture can be deployed as a brand new copy in a different region.
        """

    prompt = mode_instructions + "\n" + COMPUTE_PROMPT

    hcl = call_cloud_llm(
        prompt,
        {
            "aws_input_data": state.get("aws_input_data"),
            "user_prompt": state.get("user_prompt"),
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


def generate_data_node(state: GraphState) -> dict:
    print("[Node] Generating Data Configuration...")
    mode = state.get("deployment_mode")

    mode_instructions = ""
    if mode == "new":
        mode_instructions = f"""
        MODE: NEW INFRASTRUCTURE
        Ignore any existing AWS data. Generate the HCL strictly based on this user request:
        {state.get('user_prompt')}
        """
    elif mode == "import":
                mode_instructions = """
                MODE: IMPORT EXISTING INFRASTRUCTURE
                Read the provided AWS input data. Generate the HCL resource blocks matching the data EXACTLY.
                Additionally, you MUST generate Terraform 1.5+ `import` blocks for every resource so Terraform can adopt them.
                Example syntax:
                import {{
                    to = aws_vpc.main
                    id = "vpc-12345"
                }}
                """
    elif mode == "clone":
        mode_instructions = """
        MODE: CLONE INFRASTRUCTURE
        Read the provided AWS input data to understand the architecture. 
        DO NOT hardcode the specific AWS IDs (e.g., vpc-12345) into the HCL. 
        Parameterize the code using standard variables so this exact architecture can be deployed as a brand new copy in a different region.
        """

    prompt = mode_instructions + "\n" + DATA_PROMPT

    hcl = call_cloud_llm(
        prompt,
        {
            "aws_input_data": state.get("aws_input_data"),
            "user_prompt": state.get("user_prompt"),
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
    parse_and_write_files(hcl, phase_filename="data.tf")
    return {"data_hcl": hcl, "current_phase": "data"}


# Validation node
SKIP_VALIDATE = ("--skip-validate" in sys.argv) or (
    os.environ.get("SKIP_VALIDATE") == "1"
)


def validation_node_func(state: GraphState) -> dict:
    print("[Node] Running Validation...")

    if SKIP_VALIDATE:
        print("[Validator] SKIP_VALIDATE enabled; forcing success (dry-run).")
        return {"is_valid": True}

    validation_result = execute_terraform_validation()
    if validation_result.get("is_valid"):
        return {"is_valid": True}
    else:
        errors = state.get("error_logs", [])
        # Merge any error logs from the validator into the node state
        validator_logs = validation_result.get("error_logs", [])
        if validator_logs:
            errors.extend(validator_logs)
        else:
            # Fallback: include a textual representation if no explicit logs provided
            errors.append(str(validation_result))
        retry = state.get("retry_count", 0) + 1
        print(f"[Validator] Validation failed. Incrementing retry_count -> {retry}")
        return {"is_valid": False, "error_logs": errors, "retry_count": retry}
