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
    generate_terraform_graph,
    parse_hcl_dependencies,
)


def routing_decision_router(state: GraphState) -> str:
    """Route based on validation outcome and retry limits.

    Returns one of: 'fix_network', 'fix_security', 'fix_compute', 'fix_data', or 'complete'
    """
    if state.get("is_valid"):
        return "generate_graph"
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


def generate_network_tf(state: GraphState) -> str:
    target_vpc = state.get("target_vpc_id")
    
    if target_vpc:
        hcl = f"""
# Resolve external dependencies
data "aws_subnets" "target" {{
  filter {{
    name   = "vpc-id"
    values = ["{target_vpc}"]
  }}
}}

locals {{
  vpc_id     = "{target_vpc}"
  subnet_ids = data.aws_subnets.target.ids
}}
"""
    else:
        hcl = """
# Provision new stack
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_subnet" "public_1" {
  vpc_id     = aws_vpc.main.id
  cidr_block = "10.0.1.0/24"
}

locals {
  vpc_id     = aws_vpc.main.id
  subnet_ids = [aws_subnet.public_1.id]
}
"""
    return hcl


def generate_network_node(state: GraphState) -> dict:
    print("[Node] Generating Network Configuration...")
    hcl = generate_network_tf(state)
    parse_and_write_files(hcl, phase_filename="network.tf")
    return {"network_hcl": hcl, "current_phase": "network"}


def generate_security_node(state: GraphState) -> dict:
    print("[Node] Generating Security Configuration...")
    mode = state.get("deployment_mode")

    if mode == "import":
        aws_input = state.get("aws_input_data", {})
        resources = aws_input.get("resources", [])
        has_security = any(r.get("type") in ["aws_security_group", "aws_iam_role"] for r in resources)
        if not has_security:
            print("[Node] No security resources found in AWS input data. Bypassing LLM.")
            hcl = "# No security resources required."
            parse_and_write_files(hcl, phase_filename="security.tf")
            return {"security_hcl": hcl, "current_phase": "security"}

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
                CRITICAL: For aws_iam_role, the import `id` MUST be the Role Name (e.g., "my-role-name"), NOT the full ARN.
                NO DEFAULT TAGS: Do NOT add any default tags (like Environment, Owner, ManagedBy) if they are not explicitly present in the input JSON tags. ONLY copy the exact tags provided in the JSON telemetry. If the telemetry tags are empty, do NOT output a tags block at all.
                Example syntax:
                import {{
                    to = aws_security_group.vpc_sg
                    id = "sg-12345"
                }}
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
        prompt_user = state.get("user_prompt") + "\n\nABSOLUTE MANDATE FOR NEW MODE:\n1. NO VARIABLES ALLOWED: You are strictly FORBIDDEN from using ANY var.* references. You MUST hardcode ALL values. Hardcode cidr_blocks (e.g., '10.0.0.0/16') and tags (e.g., Environment = 'production').\n2. DEPENDENCIES & NAMING: You must strictly align resource names across files. The Network node MUST declare the VPC as 'resource \"aws_vpc\" \"main\"'. The Security node MUST declare the security group as 'resource \"aws_security_group\" \"main\"'. All cross-references must use 'local.vpc_id' and 'aws_security_group.main.id'.\n3. BLOCK SYNTAX: Never use equals signs for repeatable configuration sub-blocks. Use 'attribute { ... }' instead of 'attribute = [ ... ]', and 'ingress { ... }' instead of 'ingress = [ ... ]'.\n4. DYNAMODB SYNTAX: If you generate an aws_dynamodb_table, you must define the 'hash_key'. If you specify 'read_capacity_units' and 'write_capacity_units', you MUST explicitly set 'billing_mode = \"PROVISIONED\"'.\n5. AWS_EIP SYNTAX: For aws_eip, you MUST ONLY use 'domain = \"vpc\"'. Completely remove 'vpc = true'."
    elif mode == "import":
        prompt_user = (
            "ABSOLUTE MANDATE FOR IMPORT MODE:\n"
            "1. SCOPE: ONLY generate resources explicitly listed in aws_input_data. If the JSON only has an S3 bucket, generate ONLY an aws_s3_bucket. DO NOT generate aws_db_instance or aws_autoscaling_group unless they are in the JSON.\n"
            "2. NO REFERENCES: NEVER use Terraform cross-references. WRONG: subnet_id = aws_subnet.sub-123.id. RIGHT: subnet_id = \"subnet-123\". MUST use string literals with quotes.\n"
            "3. NO VARIABLES: NEVER use var.* syntax. WRONG: username = var.user. RIGHT: username = \"admin\". Hardcode all values.\n"
            "4. IAM ROLES: When generating an aws_iam_role, you MUST define the required 'assume_role_policy' using a standard EC2 service trust policy document inside jsonencode."
        )
    else:  # clone
        prompt_user = "ABSOLUTE MANDATE FOR CLONE MODE:\n1. PARAMETERIZATION: Replace hardcoded IDs and names from the aws_input_data JSON with var.* references.\n2. VARIABLE DECLARATION: You MUST explicitly output a 'variable \"...\" { default = \"...\" }' block with the original scanned value from the telemetry set as the default, for EVERY var.* reference you generate. Write these variable blocks AT THE VERY TOP of your output, inside the exact same HCL block as your resources. DO NOT omit them thinking they belong in a separate variables.tf file. YOU MUST WRITE THEM HERE.\nEXAMPLE REQUIRED OUTPUT:\nvariable \"vpc_id\" { default = \"vpc-12345\" }\nresource \"aws_security_group\" \"main\" { vpc_id = var.vpc_id }\n\n3. SYNTAX: Do NOT generate 'aws_vpc_gateway_attachment' resources. Associate Internet Gateways directly by setting the 'vpc_id' argument inside the 'aws_internet_gateway' block.\n4. DOMAIN RESTRICTION: You are the SECURITY node. You MUST ONLY generate security resources (aws_security_group, IAM). Completely IGNORE any subnets, instances, or S3 buckets in the JSON. CRITICAL: Because Security Groups require a VPC, you will likely parameterize the vpc_id. You MUST explicitly declare variable blocks with defaults (e.g. `variable \"vpc_id\" { default = \"...\" }`) at the top of your output alongside any other variables.\n5. SECURITY GROUP RULES: You MUST inspect the ingress and egress arrays in the security group telemetry. For each rule, generate an inline 'ingress' or 'egress' block inside the 'aws_security_group' resource. Map the 'from_port', 'to_port', 'protocol', and 'cidr_blocks'/'ipv6_cidr_blocks'/'security_groups' values accurately. Do not leave the Security Group empty.\n6. TAG DEFAULT VALUE FIDELITY: When parameterizing the 'Environment' or 'Owner' tags, set their default values to sensible production settings (e.g. environment = \"production\", owner = \"LangGraph-Agent\") rather than empty strings, if they are empty in the AWS telemetry."

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
                "An existing network configuration is available via dynamic local variables: "
                "local.vpc_id (resolves to the active VPC ID) and local.subnet_ids (list of subnet IDs). "
                "Use local.vpc_id and local.subnet_ids to bind resources dynamically. "
                "DO NOT redeclare these networking variables or resources."
            ),
        },
    )
    parse_and_write_files(hcl, phase_filename="security.tf")
    return {"security_hcl": hcl, "current_phase": "security"}


def generate_compute_node(state: GraphState) -> dict:
    print("[Node] Generating Compute Configuration...")
    mode = state.get("deployment_mode")

    if mode == "import":
        aws_input = state.get("aws_input_data", {})
        resources = aws_input.get("resources", [])
        has_compute = any(r.get("type") in ["aws_instance", "aws_autoscaling_group", "aws_launch_template"] for r in resources)
        if not has_compute:
            print("[Node] No compute resources found in AWS input data. Bypassing LLM.")
            hcl = "# No compute resources required."
            parse_and_write_files(hcl, phase_filename="compute.tf")
            return {"compute_hcl": hcl, "current_phase": "compute"}

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
                import {{
                    to = aws_instance.app
                    id = "i-0123456789abcdef0"
                }}
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
        prompt_user = state.get("user_prompt") + "\n\nABSOLUTE MANDATE FOR NEW MODE:\n1. NO VARIABLES ALLOWED: You are strictly FORBIDDEN from using ANY var.* references. You MUST hardcode ALL values. Hardcode cidr_blocks (e.g., '10.0.0.0/16') and tags (e.g., Environment = 'production').\n2. DEPENDENCIES & NAMING: You must strictly align resource names across files. The Network node MUST declare the VPC as 'resource \"aws_vpc\" \"main\"'. The Security node MUST declare the security group as 'resource \"aws_security_group\" \"main\"'. All cross-references must use 'local.vpc_id' and 'aws_security_group.main.id'.\n3. BLOCK SYNTAX: Never use equals signs for repeatable configuration sub-blocks. Use 'attribute { ... }' instead of 'attribute = [ ... ]', and 'ingress { ... }' instead of 'ingress = [ ... ]'.\n4. DYNAMODB SYNTAX: If you generate an aws_dynamodb_table, you must define the 'hash_key'. You MUST set 'billing_mode = \"PAY_PER_REQUEST\"'. Do NOT specify 'read_capacity_units' or 'write_capacity_units'.\n5. AWS_EIP SYNTAX: For aws_eip, you MUST ONLY use 'domain = \"vpc\"'. Completely remove 'vpc = true'."
    elif mode == "import":
        prompt_user = (
            "ABSOLUTE MANDATE FOR IMPORT MODE:\n"
            "1. SCOPE: ONLY generate resources explicitly listed in aws_input_data. If the JSON only has an S3 bucket, generate ONLY an aws_s3_bucket. DO NOT generate aws_db_instance or aws_autoscaling_group unless they are in the JSON.\n"
            "2. NO REFERENCES: NEVER use Terraform cross-references. WRONG: subnet_id = aws_subnet.sub-123.id. RIGHT: subnet_id = \"subnet-123\". MUST use string literals with quotes.\n"
            "3. NO VARIABLES: NEVER use var.* syntax. WRONG: username = var.user. RIGHT: username = \"admin\". Hardcode all values.\n"
            "4. DYNAMODB: If you generate an aws_dynamodb_table, you MUST set billing_mode = \"PAY_PER_REQUEST\" and you are strictly FORBIDDEN from specifying read_capacity_units or write_capacity_units.\n"
            "5. NO DEFAULT TAGS: Do NOT add any default tags (like Environment, Owner, ManagedBy) if they are not explicitly present in the input JSON tags. ONLY copy the exact tags provided in the JSON telemetry. If the telemetry tags are empty, do NOT output a tags block at all."
        )
    else:  # clone
        prompt_user = "ABSOLUTE MANDATE FOR CLONE MODE:\n1. PARAMETERIZATION: Replace hardcoded IDs and names from the aws_input_data JSON with var.* references.\n2. VARIABLE DECLARATION: You MUST explicitly output a 'variable \"...\" { default = \"...\" }' block with the original scanned value from the telemetry set as the default, for EVERY var.* reference you generate. Write these variable blocks AT THE VERY TOP of your output, inside the exact same HCL block as your resources. DO NOT omit them thinking they belong in a separate variables.tf file. YOU MUST WRITE THEM HERE.\nEXAMPLE REQUIRED OUTPUT:\nvariable \"instance_type\" { default = \"t3.micro\" }\nresource \"aws_instance\" \"app\" { instance_type = var.instance_type }\n\n3. SYNTAX: Do NOT generate 'aws_vpc_gateway_attachment' resources. Associate Internet Gateways directly by setting the 'vpc_id' argument inside the 'aws_internet_gateway' block.\n4. DOMAIN RESTRICTION: You are the COMPUTE node. You MUST ONLY generate compute resources (aws_instance, ASG, Launch Templates). Completely IGNORE any subnets, security groups, or S3 buckets in the JSON. NEVER generate aws_subnet. CRITICAL: When you parameterize your resources, you MUST explicitly declare variables with defaults (e.g. `variable \"ami_id\" { default = \"...\" }`, `variable \"instance_type\" { default = \"...\" }`, and `variable \"subnet_id\" { default = \"...\" }`) at the top of your output alongside your resources.\n5. LAUNCH TEMPLATES & BOOTSTRAPPING: When generating an aws_launch_template, you MUST inspect its telemetry fields. If 'user_data' is present, set the 'user_data' argument. If 'block_device_mappings' are present, generate the matching nested 'block_device_mappings' blocks specifying device name, EBS volume size, and type. If 'iam_instance_profile' is present, specify it inside the launch template.\n6. TAG DEFAULT VALUE FIDELITY: When parameterizing the 'Environment' or 'Owner' tags, set their default values to sensible production settings (e.g. environment = \"production\", owner = \"LangGraph-Agent\") rather than empty strings, if they are empty in the AWS telemetry."

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
                "Available network references: local.vpc_id (resolves to the active VPC ID) "
                "and local.subnet_ids (list of subnet IDs). Use these dynamic local variables to bind resources. "
                "DO NOT declare these resource blocks or local variables again."
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

    if mode == "import":
        aws_input = state.get("aws_input_data", {})
        resources = aws_input.get("resources", [])
        has_data = any(r.get("type") in ["aws_s3_bucket", "aws_db_instance", "aws_dynamodb_table"] for r in resources)
        if not has_data:
            print("[Node] No data resources found in AWS input data. Bypassing LLM.")
            hcl = "# No data resources required."
            parse_and_write_files(hcl, phase_filename="data.tf")
            return {"data_hcl": hcl, "current_phase": "data"}

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
                import {{
                    to = aws_db_instance.main
                    id = "db-ABCDEFGHIJK"
                }}
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
        prompt_user = state.get("user_prompt") + "\n\nABSOLUTE MANDATE FOR NEW MODE:\n1. NO VARIABLES ALLOWED: You are strictly FORBIDDEN from using ANY var.* references. You MUST hardcode ALL values. Hardcode cidr_blocks (e.g., '10.0.0.0/16') and tags (e.g., Environment = 'production').\n2. DEPENDENCIES & NAMING: You must strictly align resource names across files. The Network node MUST declare the VPC as 'resource \"aws_vpc\" \"main\"'. The Security node MUST declare the security group as 'resource \"aws_security_group\" \"main\"'. All cross-references must use 'aws_vpc.main.id' and 'aws_security_group.main.id'.\n3. BLOCK SYNTAX: Never use equals signs for repeatable configuration sub-blocks. Use 'attribute { ... }' instead of 'attribute = [ ... ]', and 'ingress { ... }' instead of 'ingress = [ ... ]'.\n4. DYNAMODB SYNTAX: If you generate an aws_dynamodb_table, you must define the 'hash_key'. You MUST set 'billing_mode = \"PAY_PER_REQUEST\"'. Do NOT specify 'read_capacity_units' or 'write_capacity_units'.\n5. AWS_EIP SYNTAX: For aws_eip, you MUST ONLY use 'domain = \"vpc\"'. Completely remove 'vpc = true'."
    elif mode == "import":
        prompt_user = (
            "ABSOLUTE MANDATE FOR IMPORT MODE:\n"
            "1. SCOPE: ONLY generate resources explicitly listed in aws_input_data. If the JSON only has an S3 bucket, generate ONLY an aws_s3_bucket. DO NOT generate aws_db_instance or aws_autoscaling_group unless they are in the JSON.\n"
            "2. NO REFERENCES: NEVER use Terraform cross-references. WRONG: subnet_id = aws_subnet.sub-123.id. RIGHT: subnet_id = \"subnet-123\". MUST use string literals with quotes.\n"
            "3. NO VARIABLES: NEVER use var.* syntax. WRONG: username = var.user. RIGHT: username = \"admin\". Hardcode all values.\n"
            "4. DYNAMODB: If you generate an aws_dynamodb_table, you MUST set billing_mode = \"PAY_PER_REQUEST\" and you are strictly FORBIDDEN from specifying read_capacity_units or write_capacity_units.\n"
            "5. RDS INSTANCES: When generating an aws_db_instance, if 'storage_encrypted = true' is in the telemetry, you MUST explicitly set 'storage_encrypted = true' in the resource block.\n"
            "6. NO DEFAULT TAGS: Do NOT add any default tags (like Environment, Owner, ManagedBy) if they are not explicitly present in the input JSON tags. ONLY copy the exact tags provided in the JSON telemetry. If the telemetry tags are empty, do NOT output a tags block at all."
        )
    else:  # clone
        prompt_user = "ABSOLUTE MANDATE FOR CLONE MODE:\n1. PARAMETERIZATION: Replace hardcoded IDs and names from the aws_input_data JSON with var.* references.\n2. VARIABLE DECLARATION: You MUST explicitly output a 'variable \"...\" { default = \"...\" }' block with the original scanned value from the telemetry set as the default, for EVERY var.* reference you generate. Write these variable blocks AT THE VERY TOP of your output, inside the exact same HCL block as your resources. DO NOT omit them thinking they belong in a separate variables.tf file. YOU MUST WRITE THEM HERE.\nEXAMPLE REQUIRED OUTPUT:\nvariable \"s3_bucket_name\" { default = \"my-scanned-bucket\" }\nresource \"aws_s3_bucket\" \"main\" { bucket = var.s3_bucket_name }\n\n3. SYNTAX: Do NOT generate 'aws_vpc_gateway_attachment' resources. Associate Internet Gateways directly by setting the 'vpc_id' argument inside the 'aws_internet_gateway' block.\n4. DOMAIN RESTRICTION: You are the DATA node. You MUST ONLY generate data/storage resources (aws_s3_bucket, RDS). Completely IGNORE any subnets, instances, or security groups in the JSON. NEVER generate aws_subnet or aws_security_group.\n5. S3 & DYNAMODB CONFIG: When generating an aws_s3_bucket, if the telemetry contains 'versioning' or 'server_side_encryption' settings, map them using nested blocks (e.g. `versioning { enabled = ... }` or `server_side_encryption_configuration`). When generating an aws_dynamodb_table, you MUST inspect the 'attribute_definitions' and 'hash_key'/'range_key' lists in the telemetry and define the 'attribute' block and keys matching them exactly.\n6. TAG DEFAULT VALUE FIDELITY: When parameterizing the 'Environment' or 'Owner' tags, set their default values to sensible production settings (e.g. environment = \"production\", owner = \"LangGraph-Agent\") rather than empty strings, if they are empty in the AWS telemetry."

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
                "Available network references: local.vpc_id (resolves to the active VPC ID) "
                "and local.subnet_ids (list of subnet IDs). Use these dynamic local variables to bind resources. "
                "DO NOT declare these resource blocks or local variables again."
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


def pre_flight_validation_node(state: GraphState) -> GraphState:
    print("[Node] Running Pre-flight DAG Validation...")
    dependencies = parse_hcl_dependencies()
    state["dependency_map"] = dependencies

    visited = set()
    recursion_stack = set()
    
    def detect_cycle(node):
        visited.add(node)
        recursion_stack.add(node)
        for neighbor in dependencies.get(node, []):
            if neighbor not in visited:
                if detect_cycle(neighbor):
                    return True
            elif neighbor in recursion_stack:
                return True
        recursion_stack.remove(node)
        return False

    for resource in dependencies:
        if resource not in visited:
            if detect_cycle(resource):
                raise ValueError(f"Pre-flight Validation Failed: Cyclic dependency involving {resource}.")
                
    return state # Pass state forward if DAG is strictly acyclic


def generate_graph_node(state: GraphState) -> GraphState:
    print("[Node] Generating visual dependency graph...")
    workspace_path = "terraform_workspace"
    generate_terraform_graph(workspace_path)
    return state
