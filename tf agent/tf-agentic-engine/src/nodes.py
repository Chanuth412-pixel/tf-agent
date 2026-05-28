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

    # If validation failed but retries remain, restart the pipeline from the network generation
    # to give the whole graph a chance to correct cascading issues.
    if not state.get("is_valid") and 0 < state.get("retry_count", 0) < state.get("max_retries", 3):
        print("[Router] Validation failed; routing back to network for full re-run.")
        return "fix_network"

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
        You are the NETWORK generation node.
        ONLY generate network-specific resources (VPCs, Subnets, IGWs, NAT, Route Tables).
                Do NOT generate compute, security, or database resources.
                Do NOT generate or import Security Groups (aws_security_group) or IAM roles. Security Groups belong strictly to the SECURITY node.
                Build the requested architecture from scratch using standard Terraform cross-references (for example: `vpc_id = aws_vpc.main.id`).
                Explicitly declare any required variables using `variable` blocks at the top of your output and reference them where appropriate.

                If the user prompt does not require network resources, output exactly: # No network resources required.

                User Request: {state.get('user_prompt')}
        """
    elif mode == "import":
                mode_instructions = """
                MODE: IMPORT EXISTING INFRASTRUCTURE
                You are the NETWORK generation node.
                Read the provided AWS input data. ONLY generate network-specific resources (VPCs, Subnets, IGWs, NAT, Route Tables) that match the input EXACTLY.
                NEVER use `var.*` references. EXAMPLE WRONG: image_id = var.ami_id  EXAMPLE RIGHT: image_id = "ami-0c55b159cbfafe1f0"
                EXAMPLE WRONG: instance_type = var.instance_type  EXAMPLE RIGHT: instance_type = "t3.micro"
                EXAMPLE WRONG: Environment = var.environment  EXAMPLE RIGHT: Environment = "production"
                ONLY use the exact hardcoded AWS IDs provided in the input JSON data (e.g., "vpc-12345", "subnet-67890"). IF an ID is missing, use a placeholder string (e.g., "subnet-000000").
                Do NOT generate or import Security Groups (aws_security_group) or IAM roles. Security Groups belong strictly to the SECURITY node.
                Additionally, you MUST generate Terraform 1.5+ `import` blocks for every resource so Terraform can adopt them.
                Example syntax:
                import {{{{
                    to = aws_vpc.main
                    id = "vpc-12345"
                }}}}
                If the aws_input_data contains no network resources, output exactly: # No network resources required.
                """
    elif mode == "clone":
                mode_instructions = """
                MODE: CLONE INFRASTRUCTURE
                You are the NETWORK generation node.
                Read the provided AWS input data to understand the architecture. ONLY generate network-specific resources.
                Replace hardcoded IDs from the JSON with `var.*` references and you MUST include corresponding `variable` declaration blocks for every variable you create.
                Parameterize values using variables so this architecture can be deployed as a brand new copy in a different region.
                Do NOT generate or import Security Groups (aws_security_group) or IAM roles. Security Groups belong strictly to the SECURITY node.
                If the aws_input_data contains no network resources, output exactly: # No network resources required.
                """

    prompt = mode_instructions + "\n" + NETWORK_PROMPT

    if mode == "new":
        prompt_user = state.get("user_prompt") + "\n\nABSOLUTE MANDATE FOR NEW MODE:\n1. NO VARIABLES ALLOWED: You are strictly FORBIDDEN from using ANY var.* references. You MUST hardcode ALL values. Hardcode cidr_blocks (e.g., '10.0.0.0/16') and tags (e.g., Environment = 'production'). NEVER use var.vpc_cidr, var.environment, var.owner, var.ami, or var.instance_type.\n2. DEPENDENCIES: Do not hallucinate cross-references. You MUST EXACTLY use 'aws_security_group.main.id' for all security group references in the Compute node. NEVER use names like 'eks_worker' or 'eks_cluster'. The Security node MUST explicitly define 'resource \"aws_security_group\" \"main\"'.\n3. SYNTAX: For aws_eip, you MUST ONLY use 'domain = \"vpc\"'. You MUST COMPLETELY REMOVE 'vpc = true' from your code."
    elif mode == "import":
        prompt_user = (
            "ABSOLUTE MANDATE FOR IMPORT MODE:\n"
            "1. SCOPE: ONLY generate resources explicitly listed in aws_input_data. If the JSON only has an S3 bucket, generate ONLY an aws_s3_bucket. DO NOT generate aws_db_instance or aws_autoscaling_group unless they are in the JSON.\n"
            "2. NO REFERENCES: NEVER use Terraform cross-references. WRONG: subnet_id = aws_subnet.sub-123.id. RIGHT: subnet_id = \"subnet-123\". MUST use string literals with quotes.\n"
            "3. NO VARIABLES: NEVER use var.* syntax. WRONG: username = var.user. RIGHT: username = \"admin\". Hardcode all values."
        )
    else:  # clone
        prompt_user = "ABSOLUTE MANDATE FOR CLONE MODE:\n1. PARAMETERIZATION: Replace hardcoded IDs and names from the aws_input_data JSON with var.* references.\n2. VARIABLE DECLARATION: You MUST explicitly output a 'variable \"...\" {}' block for EVERY var.* reference you generate. Write these variable blocks AT THE VERY TOP of your output, inside the exact same HCL block as your resources. DO NOT omit them thinking they belong in a separate variables.tf file. YOU MUST WRITE THEM HERE.\nEXAMPLE REQUIRED OUTPUT:\nvariable \"vpc_cidr\" {}\nresource \"aws_vpc\" \"main\" { cidr_block = var.vpc_cidr }\n\n3. SYNTAX: Do NOT generate 'aws_vpc_gateway_attachment' resources. Associate Internet Gateways directly by setting the 'vpc_id' argument inside the 'aws_internet_gateway' block.\n4. DOMAIN RESTRICTION: You are the NETWORK node. You MUST ONLY generate networking resources (VPCs, aws_subnet, IGWs, routing). Completely IGNORE any instances, security groups, or S3 buckets in the JSON."


def generate_security_node(state: GraphState) -> dict:
    print("[Node] Generating Security Configuration...")
    mode = state.get("deployment_mode")

    mode_instructions = ""
    if mode == "new":
        mode_instructions = f"""
        MODE: NEW INFRASTRUCTURE
        You are the SECURITY generation node.
        ONLY generate security-specific resources (Security Groups, Network ACLs, IAM roles and policies).
                Do NOT generate network, compute, or database resources.
                Build the requested security architecture from scratch using standard Terraform cross-references and explicitly define any required `variable` blocks for tunables.

                If the user prompt does not require security resources, output exactly: # No security resources required.

                User Request: {state.get('user_prompt')}
        """
    elif mode == "import":
                mode_instructions = """
                MODE: IMPORT EXISTING INFRASTRUCTURE
                You are the SECURITY generation node.
                Read the provided AWS input data. ONLY generate security-specific resources (Security Groups, Network ACLs, IAM roles and policies) that match the input EXACTLY.
                NEVER use `var.*` references. EXAMPLE WRONG: image_id = var.ami_id  EXAMPLE RIGHT: image_id = "ami-0c55b159cbfafe1f0"
                EXAMPLE WRONG: instance_type = var.instance_type  EXAMPLE RIGHT: instance_type = "t3.micro"
                EXAMPLE WRONG: Environment = var.environment  EXAMPLE RIGHT: Environment = "production"
                ONLY use the exact hardcoded AWS IDs provided in the input JSON data. IF an ID is missing, use a placeholder string.
                Additionally, you MUST generate Terraform 1.5+ `import` blocks for every resource so Terraform can adopt them.
                Example syntax:
                import {{{{
                    to = aws_security_group.vpc_sg
                    id = "sg-12345"
                }}}}
                If the aws_input_data contains no security resources, output exactly: # No security resources required.
                """
    elif mode == "clone":
                mode_instructions = """
                MODE: CLONE INFRASTRUCTURE
                You are the SECURITY generation node.
                Read the provided AWS input data to understand the architecture. ONLY generate security-specific resources.
                Replace hardcoded IDs from the JSON with `var.*` references and include `variable` declaration blocks for every variable created. Parameterize values using variables so this architecture can be deployed as a brand new copy in a different region.
                If the aws_input_data contains no security resources, output exactly: # No security resources required.
                """

    prompt = mode_instructions + "\n" + SECURITY_PROMPT

    if mode == "new":
        prompt_user = state.get("user_prompt") + "\n\nABSOLUTE MANDATE FOR NEW MODE:\n1. NO VARIABLES ALLOWED: You are strictly FORBIDDEN from using ANY var.* references. You MUST hardcode ALL values. Hardcode cidr_blocks (e.g., '10.0.0.0/16') and tags (e.g., Environment = 'production'). NEVER use var.vpc_cidr, var.environment, var.owner, var.ami, or var.instance_type.\n2. DEPENDENCIES: Do not hallucinate cross-references. You MUST EXACTLY use 'aws_security_group.main.id' for all security group references in the Compute node. NEVER use names like 'eks_worker' or 'eks_cluster'. The Security node MUST explicitly define 'resource \"aws_security_group\" \"main\"'.\n3. SYNTAX: For aws_eip, you MUST ONLY use 'domain = \"vpc\"'. You MUST COMPLETELY REMOVE 'vpc = true' from your code."
    elif mode == "import":
        prompt_user = (
            "ABSOLUTE MANDATE FOR IMPORT MODE:\n"
            "1. SCOPE: ONLY generate resources explicitly listed in aws_input_data. If the JSON only has an S3 bucket, generate ONLY an aws_s3_bucket. DO NOT generate aws_db_instance or aws_autoscaling_group unless they are in the JSON.\n"
            "2. NO REFERENCES: NEVER use Terraform cross-references. WRONG: subnet_id = aws_subnet.sub-123.id. RIGHT: subnet_id = \"subnet-123\". MUST use string literals with quotes.\n"
            "3. NO VARIABLES: NEVER use var.* syntax. WRONG: username = var.user. RIGHT: username = \"admin\". Hardcode all values."
        )
    else:  # clone
        prompt_user = "ABSOLUTE MANDATE FOR CLONE MODE:\n1. PARAMETERIZATION: Replace hardcoded IDs and names from the aws_input_data JSON with var.* references.\n2. VARIABLE DECLARATION: You MUST explicitly output a 'variable \"...\" {}' block for EVERY var.* reference you generate. Write these variable blocks AT THE VERY TOP of your output, inside the exact same HCL block as your resources. DO NOT omit them thinking they belong in a separate variables.tf file. YOU MUST WRITE THEM HERE.\nEXAMPLE REQUIRED OUTPUT:\nvariable \"vpc_cidr\" {}\nresource \"aws_vpc\" \"main\" { cidr_block = var.vpc_cidr }\n\n3. SYNTAX: Do NOT generate 'aws_vpc_gateway_attachment' resources. Associate Internet Gateways directly by setting the 'vpc_id' argument inside the 'aws_internet_gateway' block.\n4. DOMAIN RESTRICTION: You are the SECURITY node. You MUST ONLY generate security resources (aws_security_group, IAM). Completely IGNORE any subnets, instances, or S3 buckets in the JSON. CRITICAL: Because Security Groups require a VPC, you will likely parameterize the vpc_id. You MUST explicitly declare 'variable \"vpc_id\" {}' at the top of your output alongside any other variables."

    # If there are validation results from a previous run, prepend them
    val_errors = state.get("validation_results", "").replace("{", "{{").replace("}", "}}")
    if val_errors:
        prompt = val_errors + "\n" + prompt

    hcl = call_cloud_llm(
        prompt,
        {
            "aws_input_data": state.get("aws_input_data"),
            "user_prompt": prompt_user,
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
        You are the COMPUTE generation node.
        ONLY generate compute-specific resources (EC2 instances, EKS resources, Launch Templates, AutoScaling, etc.).
                Do NOT generate network, security, or database resources.
                Build the requested compute architecture from scratch using standard Terraform cross-references (e.g., `subnet_id = aws_subnet.public_1.id`) and explicitly define any required `variable` blocks for tunables like AMI and instance type.

                If the user prompt does not require compute resources, output exactly: # No compute resources required.

                User Request: {state.get('user_prompt')}
        """
    elif mode == "import":
                mode_instructions = """
                MODE: IMPORT EXISTING INFRASTRUCTURE
                You are the COMPUTE generation node.
                Read the provided AWS input data. ONLY generate compute-specific resources (EC2 instances, EKS resources, Launch Templates, AutoScaling) that match the input EXACTLY.
                NEVER use `var.*` references. EXAMPLE WRONG: image_id = var.ami_id  EXAMPLE RIGHT: image_id = "ami-0c55b159cbfafe1f0"
                EXAMPLE WRONG: instance_type = var.instance_type  EXAMPLE RIGHT: instance_type = "t3.micro"
                EXAMPLE WRONG: Environment = var.environment  EXAMPLE RIGHT: Environment = "production"
                ONLY use the exact hardcoded AWS IDs provided in the input JSON data. IF an ID is missing, use a placeholder string.
                Additionally, you MUST generate Terraform 1.5+ `import` blocks for every resource so Terraform can adopt them.
                Example syntax:
                import {{{{
                    to = aws_instance.app
                    id = "i-0123456789abcdef0"
                }}}}
                If the aws_input_data contains no compute resources, output exactly: # No compute resources required.
                """
    elif mode == "clone":
                mode_instructions = """
                MODE: CLONE INFRASTRUCTURE
                You are the COMPUTE generation node.
                Read the provided AWS input data to understand the architecture. ONLY generate compute-specific resources.
                Replace hardcoded IDs from the JSON with `var.*` references and include corresponding `variable` declaration blocks for every variable you create. Parameterize values using variables so this architecture can be deployed as a brand new copy in a different region.
                If the aws_input_data contains no compute resources, output exactly: # No compute resources required.
                """

    prompt = mode_instructions + "\n" + COMPUTE_PROMPT

    if mode == "new":
        prompt_user = state.get("user_prompt") + "\n\nABSOLUTE MANDATE FOR NEW MODE:\n1. NO VARIABLES ALLOWED: You are strictly FORBIDDEN from using ANY var.* references. You MUST hardcode ALL values. Hardcode cidr_blocks (e.g., '10.0.0.0/16') and tags (e.g., Environment = 'production'). NEVER use var.vpc_cidr, var.environment, var.owner, var.ami, or var.instance_type.\n2. DEPENDENCIES: Do not hallucinate cross-references. You MUST EXACTLY use 'aws_security_group.main.id' for all security group references in the Compute node. NEVER use names like 'eks_worker' or 'eks_cluster'. The Security node MUST explicitly define 'resource \"aws_security_group\" \"main\"'.\n3. SYNTAX: For aws_eip, you MUST ONLY use 'domain = \"vpc\"'. You MUST COMPLETELY REMOVE 'vpc = true' from your code."
    elif mode == "import":
        prompt_user = (
            "ABSOLUTE MANDATE FOR IMPORT MODE:\n"
            "1. SCOPE: ONLY generate resources explicitly listed in aws_input_data. If the JSON only has an S3 bucket, generate ONLY an aws_s3_bucket. DO NOT generate aws_db_instance or aws_autoscaling_group unless they are in the JSON.\n"
            "2. NO REFERENCES: NEVER use Terraform cross-references. WRONG: subnet_id = aws_subnet.sub-123.id. RIGHT: subnet_id = \"subnet-123\". MUST use string literals with quotes.\n"
            "3. NO VARIABLES: NEVER use var.* syntax. WRONG: username = var.user. RIGHT: username = \"admin\". Hardcode all values."
        )
    else:  # clone
        prompt_user = "ABSOLUTE MANDATE FOR CLONE MODE:\n1. PARAMETERIZATION: Replace hardcoded IDs and names from the aws_input_data JSON with var.* references.\n2. VARIABLE DECLARATION: You MUST explicitly output a 'variable \"...\" {}' block for EVERY var.* reference you generate. Write these variable blocks AT THE VERY TOP of your output, inside the exact same HCL block as your resources. DO NOT omit them thinking they belong in a separate variables.tf file. YOU MUST WRITE THEM HERE.\nEXAMPLE REQUIRED OUTPUT:\nvariable \"vpc_cidr\" {}\nresource \"aws_vpc\" \"main\" { cidr_block = var.vpc_cidr }\n\n3. SYNTAX: Do NOT generate 'aws_vpc_gateway_attachment' resources. Associate Internet Gateways directly by setting the 'vpc_id' argument inside the 'aws_internet_gateway' block.\n4. DOMAIN RESTRICTION: You are the COMPUTE node. You MUST ONLY generate compute resources (aws_instance, ASG, Launch Templates). Completely IGNORE any subnets, security groups, or S3 buckets in the JSON. NEVER generate aws_subnet."

    # If there are validation results from a previous run, prepend them
    val_errors = state.get("validation_results", "").replace("{", "{{").replace("}", "}}")
    if val_errors:
        prompt = val_errors + "\n" + prompt

    hcl = call_cloud_llm(
        prompt,
        {
            "aws_input_data": state.get("aws_input_data"),
            "user_prompt": prompt_user,
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
        You are the DATA generation node.
        ONLY generate data-specific resources (RDS instances, DB subnet groups, S3 buckets, DynamoDB, etc.).
                Do NOT generate network, compute, or security resources.
                Build the requested data architecture from scratch using standard Terraform cross-references and explicitly define any required `variable` blocks for tunables.

                If the user prompt does not require data resources, output exactly: # No data resources required.

                User Request: {state.get('user_prompt')}
        """
    elif mode == "import":
                mode_instructions = """
                MODE: IMPORT EXISTING INFRASTRUCTURE
                You are the DATA generation node.
                Read the provided AWS input data. ONLY generate data-specific resources (RDS instances, DB subnet groups, S3 buckets, DynamoDB) that match the input EXACTLY.
                NEVER use `var.*` references. EXAMPLE WRONG: image_id = var.ami_id  EXAMPLE RIGHT: image_id = "ami-0c55b159cbfafe1f0"
                EXAMPLE WRONG: instance_type = var.instance_type  EXAMPLE RIGHT: instance_type = "t3.micro"
                EXAMPLE WRONG: Environment = var.environment  EXAMPLE RIGHT: Environment = "production"
                ONLY use the exact hardcoded AWS IDs provided in the input JSON data. IF an ID is missing, use a placeholder string.
                Additionally, you MUST generate Terraform 1.5+ `import` blocks for every resource so Terraform can adopt them.
                Example syntax:
                import {{{{
                    to = aws_db_instance.main
                    id = "db-ABCDEFGHIJK"
                }}}}
                If the aws_input_data contains no data resources, output exactly: # No data resources required.
                """
    elif mode == "clone":
                mode_instructions = """
                MODE: CLONE INFRASTRUCTURE
                You are the DATA generation node.
                Read the provided AWS input data to understand the architecture. ONLY generate data-specific resources.
                Replace hardcoded IDs from the JSON with `var.*` references and include corresponding `variable` declaration blocks for every variable you create. Parameterize values using variables so this architecture can be deployed as a brand new copy in a different region.
                If the aws_input_data contains no data resources, output exactly: # No data resources required.
                """

    prompt = mode_instructions + "\n" + DATA_PROMPT

    if mode == "new":
        prompt_user = state.get("user_prompt") + "\n\nABSOLUTE MANDATE FOR NEW MODE:\n1. NO VARIABLES ALLOWED: You are strictly FORBIDDEN from using ANY var.* references. You MUST hardcode ALL values. Hardcode cidr_blocks (e.g., '10.0.0.0/16') and tags (e.g., Environment = 'production'). NEVER use var.vpc_cidr, var.environment, var.owner, var.ami, or var.instance_type.\n2. DEPENDENCIES: Do not hallucinate cross-references. You MUST EXACTLY use 'aws_security_group.main.id' for all security group references in the Compute node. NEVER use names like 'eks_worker' or 'eks_cluster'. The Security node MUST explicitly define 'resource \"aws_security_group\" \"main\"'.\n3. SYNTAX: For aws_eip, you MUST ONLY use 'domain = \"vpc\"'. You MUST COMPLETELY REMOVE 'vpc = true' from your code."
    elif mode == "import":
        prompt_user = (
            "ABSOLUTE MANDATE FOR IMPORT MODE:\n"
            "1. SCOPE: ONLY generate resources explicitly listed in aws_input_data. If the JSON only has an S3 bucket, generate ONLY an aws_s3_bucket. DO NOT generate aws_db_instance or aws_autoscaling_group unless they are in the JSON.\n"
            "2. NO REFERENCES: NEVER use Terraform cross-references. WRONG: subnet_id = aws_subnet.sub-123.id. RIGHT: subnet_id = \"subnet-123\". MUST use string literals with quotes.\n"
            "3. NO VARIABLES: NEVER use var.* syntax. WRONG: username = var.user. RIGHT: username = \"admin\". Hardcode all values."
        )
    else:  # clone
        prompt_user = "ABSOLUTE MANDATE FOR CLONE MODE:\n1. PARAMETERIZATION: Replace hardcoded IDs and names from the aws_input_data JSON with var.* references.\n2. VARIABLE DECLARATION: You MUST explicitly output a 'variable \"...\" {}' block for EVERY var.* reference you generate. Write these variable blocks AT THE VERY TOP of your output, inside the exact same HCL block as your resources. DO NOT omit them thinking they belong in a separate variables.tf file. YOU MUST WRITE THEM HERE.\nEXAMPLE REQUIRED OUTPUT:\nvariable \"vpc_cidr\" {}\nresource \"aws_vpc\" \"main\" { cidr_block = var.vpc_cidr }\n\n3. SYNTAX: Do NOT generate 'aws_vpc_gateway_attachment' resources. Associate Internet Gateways directly by setting the 'vpc_id' argument inside the 'aws_internet_gateway' block.\n4. DOMAIN RESTRICTION: You are the DATA node. You MUST ONLY generate data/storage resources (aws_s3_bucket, RDS). Completely IGNORE any subnets, instances, or security groups in the JSON. NEVER generate aws_subnet or aws_security_group."

    # If there are validation results from a previous run, prepend them
    val_errors = state.get("validation_results", "").replace("{", "{{").replace("}", "}}")
    if val_errors:
        prompt = val_errors + "\n" + prompt

    hcl = call_cloud_llm(
        prompt,
        {
            "aws_input_data": state.get("aws_input_data"),
            "user_prompt": prompt_user,
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

        # Build a single validation_results string that prepends the
        # required CRITICAL instruction before the raw Terraform errors.
        CRITICAL_RETRY_INSTRUCTION = (
            "CRITICAL RETRY INSTRUCTION: The errors below occurred because you hallucinated resources not present in the aws_input_data JSON. "
            "If Terraform reports missing variables or unsupported arguments for resources like aws_autoscaling_group, aws_db_instance, or aws_launch_template, "
            "DO NOT attempt to fix them by adding variables or blocks. You MUST completely DELETE those resource blocks from your code. "
            "Limit your output strictly to the resources provided in the JSON."
        )

        joined_errors = "\n".join(errors)
        validation_results = CRITICAL_RETRY_INSTRUCTION + "\n\n" + joined_errors

        retry = state.get("retry_count", 0) + 1
        print(f"[Validator] Validation failed. Incrementing retry_count -> {retry}")
        return {
            "is_valid": False,
            "error_logs": errors,
            "validation_results": validation_results,
            "retry_count": retry,
        }
