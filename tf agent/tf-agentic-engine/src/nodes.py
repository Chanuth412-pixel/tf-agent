import os
import sys
from src.state import GraphState, create_initial_state
from src.utils import (
    call_cloud_llm,
    parse_and_write_files,
    execute_terraform_validation,
    scrub_workspace_variables,
    NETWORK_PROMPT,
    SECURITY_PROMPT,
    COMPUTE_PROMPT,
    DATA_PROMPT,
    filter_aws_input_data,
    COMPLIANCE_RULES,
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
    aws_input = filter_aws_input_data(state.get("aws_input_data", {}), "network")

    if mode == "import":
        vpc_id = aws_input.get("vpc_id")
        resources = aws_input.get("resources", [])
        has_network = bool(vpc_id) or any(r.get("type") in ["aws_subnet", "aws_vpc"] for r in resources)
        if not has_network:
            print("[Node] No network resources found in AWS input data. Bypassing LLM.")
            hcl = "# No network resources required."
            parse_and_write_files(hcl, phase_filename="network.tf")
            return {"network_hcl": hcl, "current_phase": "network"}

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
                ONLY use the exact hardcoded AWS IDs provided in the input JSON data (e.g., "vpc-12345", "subnet-67890"). If a resource is listed in the JSON but its ID is missing, use a placeholder.
                STRICT SCOPE: You MUST ONLY generate resources that are explicitly present in the `aws_input_data`. If `aws_internet_gateway` is NOT present in the input JSON resources list, do NOT generate any `aws_internet_gateway` resource or any `import` block for it. Similarly, if only one subnet is present in the telemetry, do NOT generate a second subnet or any placeholders for it.
                Do NOT generate or import Security Groups (aws_security_group) or IAM roles. Security Groups belong strictly to the SECURITY node.
                Additionally, you MUST generate Terraform 1.5+ `import` blocks for every resource so Terraform can adopt them.
                CRITICAL IMPORT BLOCK RULES:
                For every resource generated, you MUST output a corresponding 'import' block.
                You must extract the correct identifying string from the input JSON based on this strict mapping matrix:

                1. aws_vpc -> Use the 'VpcId' (e.g., vpc-12345)
                2. aws_subnet -> Use the 'SubnetId' (e.g., subnet-67890)
                3. aws_security_group -> Use the 'GroupId' (e.g., sg-11111)
                4. aws_instance -> Use the 'InstanceId' (e.g., i-22222)
                5. aws_db_instance -> Use the DB 'DBInstanceIdentifier' name, NOT the ARN.
                6. aws_db_subnet_group -> Use the 'DBSubnetGroupName' string.

                Format the import block exactly like this at the top of the file:
                import {{
                  to = resource_type.local_name
                  id = "EXACT_MAPPED_STRING"
                }}
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

    network_constraint = """
    CONSTRAINT: You are strictly responsible for Networking. Only generate `resource` and `import` blocks for VPCs, Subnets, NAT Gateways, and Internet Gateways.
    
    CRITICAL ARCHITECTURAL CONSTRAINT:
    You must process EVERY resource provided in the routed payload. 
    If the payload contains multiple independent VPCs or environments (e.g., prod and staging), you must generate separate infrastructure trees for EACH environment. 
    Do NOT drop environments. Do NOT synthesize unrequested auxiliary resources like extra subnets or internet gateways unless they are explicitly present in the input JSON data.

    CRITICAL FEW-SHOT EXAMPLE:
    You must map the exact 'id' from the JSON to the Terraform resource name. You must write a separate block for every single item in the list. Do not add anything else.

    If the JSON is:
    [
      {"type": "aws_vpc", "id": "vpc-core-prod", "cidr_block": "10.10.0.0/16"},
      {"type": "aws_vpc", "id": "vpc-core-staging", "cidr_block": "10.20.0.0/16"}
    ]

    Your output MUST BE exactly:
    resource "aws_vpc" "vpc_core_prod" {
      cidr_block = "10.10.0.0/16"
      tags = {
        Name = "vpc-core-prod"
      }
    }

    resource "aws_vpc" "vpc_core_staging" {
      cidr_block = "10.20.0.0/16"
      tags = {
        Name = "vpc-core-staging"
      }
    }

    CRITICAL INSTRUCTION: You are currently failing to map the staging resources. 
    You MUST output a terraform resource block for:
    - vpc-core-prod AND vpc-core-staging
    - subnet-web-prod AND subnet-web-staging
    - server-web-prod AND server-web-staging

    If you output 'prod' without also outputting 'staging', your execution will be terminated. Do not summarize. Output the exact blocks.
    """
    prompt = mode_instructions + "\n" + network_constraint + "\n" + NETWORK_PROMPT

    if mode == "new":
        prompt_user = state.get("user_prompt") + "\n\nABSOLUTE MANDATE FOR NEW MODE:\n1. NO VARIABLES ALLOWED: You are strictly FORBIDDEN from using ANY var.* references. You MUST hardcode ALL values. Hardcode cidr_blocks (e.g., '10.0.0.0/16') and tags (e.g., Environment = 'production').\n2. DEPENDENCIES & NAMING: You must strictly align resource names across files. The Network node MUST declare the VPC as 'resource \"aws_vpc\" \"main\"'. The Security node MUST declare the security group as 'resource \"aws_security_group\" \"main\"'. All cross-references must use 'aws_vpc.main.id' and 'aws_security_group.main.id'.\n3. BLOCK SYNTAX: Never use equals signs for repeatable configuration sub-blocks. Use 'attribute { ... }' instead of 'attribute = [ ... ]', and 'ingress { ... }' instead of 'ingress = [ ... ]'.\n4. DYNAMODB SYNTAX: If you generate an aws_dynamodb_table, you must define the 'hash_key'. You MUST set 'billing_mode = \"PAY_PER_REQUEST\"'. Do NOT specify 'read_capacity_units' or 'write_capacity_units'.\n5. AWS_EIP SYNTAX: For aws_eip, you MUST ONLY use 'domain = \"vpc\"'. Completely remove 'vpc = true'."
    elif mode == "import":
        prompt_user = (
            "ABSOLUTE MANDATE FOR IMPORT MODE:\n"
            "1. SCOPE: ONLY generate resources explicitly listed in aws_input_data. If the JSON only has an S3 bucket, generate ONLY an aws_s3_bucket. DO NOT generate aws_db_instance or aws_autoscaling_group unless they are in the JSON.\n"
            "2. NO REFERENCES: NEVER use Terraform cross-references. WRONG: subnet_id = aws_subnet.sub-123.id. RIGHT: subnet_id = \"subnet-123\". MUST use string literals with quotes.\n"
            "3. NO VARIABLES: NEVER use var.* syntax. WRONG: username = var.user. RIGHT: username = \"admin\". Hardcode all values.\n"
            "4. DYNAMODB: If you generate an aws_dynamodb_table, you MUST set billing_mode = \"PAY_PER_REQUEST\" and you are strictly FORBIDDEN from specifying read_capacity_units or write_capacity_units.\n"
            "5. NO DEFAULT TAGS: Do NOT add any default tags (like Environment, Owner, ManagedBy) if they are not explicitly present in the input JSON tags. ONLY copy the exact tags provided in the JSON telemetry. If the telemetry tags are empty, do NOT output a tags block at all.\n"
            "6. CRITICAL IMPORT BLOCK RULES:\n"
            "   For every resource generated, you MUST output a corresponding 'import' block.\n"
            "   You must extract the correct identifying string from the input JSON based on this strict mapping matrix:\n"
            "   1. aws_vpc -> Use the 'VpcId' (e.g., vpc-12345)\n"
            "   2. aws_subnet -> Use the 'SubnetId' (e.g., subnet-67890)\n"
            "   3. aws_security_group -> Use the 'GroupId' (e.g., sg-11111)\n"
            "   4. aws_instance -> Use the 'InstanceId' (e.g., i-22222)\n"
            "   5. aws_db_instance -> Use the DB 'DBInstanceIdentifier' name, NOT the ARN.\n"
            "   6. aws_db_subnet_group -> Use the 'DBSubnetGroupName' string.\n\n"
            "   Format the import block exactly like this at the top of the file:\n"
            "   import {\n"
            "     to = resource_type.local_name\n"
            "     id = \"EXACT_MAPPED_STRING\"\n"
            "   }"
        )
    else:  # clone
        prompt_user = "ABSOLUTE MANDATE FOR CLONE MODE:\n1. PARAMETERIZATION: Replace hardcoded IDs and names from the aws_input_data JSON with var.* references.\n2. VARIABLE DECLARATION: You MUST explicitly output a 'variable \"...\" { default = \"...\" }' block with the original scanned value from the telemetry set as the default, for EVERY var.* reference you generate. Write these variable blocks AT THE VERY TOP of your output, inside the exact same HCL block as your resources. DO NOT omit them thinking they belong in a separate variables.tf file. YOU MUST WRITE THEM HERE.\nEXAMPLE REQUIRED OUTPUT:\nvariable \"vpc_cidr\" { default = \"10.0.0.0/16\" }\nresource \"aws_vpc\" \"main\" { cidr_block = var.vpc_cidr }\n\n3. SYNTAX: Do NOT generate 'aws_vpc_gateway_attachment' resources. Associate Internet Gateways directly by setting the 'vpc_id' argument inside the 'aws_internet_gateway' block.\n4. DOMAIN RESTRICTION: You are the NETWORK node. You MUST ONLY generate networking resources (VPCs, aws_subnet, IGWs, routing). Completely IGNORE any instances, security groups, or S3 buckets in the JSON."

    # If there are validation results from a previous run, prepend them
    val_errors = state.get("validation_results", "").replace("{", "{{").replace("}", "}}")
    if val_errors:
        prompt = val_errors + "\n" + prompt

    resources = aws_input.get("resources", [])
    if not resources:
        hcl = "# No network resources required."
    else:
        hcl_blocks = []
        for resource in resources:
            single_input = {
                "region": aws_input.get("region"),
                "vpc_id": aws_input.get("vpc_id"),
                "resources": [resource]
            }
            single_prompt = f"""
            You are a strict, literal Terraform translator.
            Generate the HCL block for THIS EXACT RESOURCE ONLY:
            Type: {resource.get('type')}
            ID/Name: {resource.get('id') or resource.get('name')}
            
            CRITICAL DIRECTIVES:
            1. ONLY output the HCL block for the single resource defined above.
            2. DO NOT synthesize any other resources (like VPCs, subnets, internet gateways, or route tables) unless it is this exact resource.
            3. You MUST use the exact resource identifier/name (cleaned to replace hyphens with underscores in local labels) as the Terraform resource block identifier. Do NOT rename it 'main', 'public', or 'private'.
            4. In import blocks, the 'to' attribute MUST be exactly '{resource.get('type')}.{resource.get('id') or resource.get('name')}' (or cleaned label).
            """ + "\n" + mode_instructions + "\n" + network_constraint + "\n" + COMPLIANCE_RULES
            
            if val_errors:
                single_prompt = val_errors + "\n" + single_prompt
                
            block = call_cloud_llm(
                single_prompt,
                {
                    "aws_input_data": single_input,
                    "user_prompt": prompt_user,
                },
            )
            hcl_blocks.append(block)
        hcl = "\n\n".join(hcl_blocks)
        
    parse_and_write_files(hcl, phase_filename="network.tf")
    return {"network_hcl": hcl, "current_phase": "network"}


def generate_security_node(state: GraphState) -> dict:
    print("[Node] Generating Security Configuration...")
    mode = state.get("deployment_mode")
    aws_input = filter_aws_input_data(state.get("aws_input_data", {}), "security")

    if mode == "import":
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
                CRITICAL IMPORT BLOCK RULES:
                For every resource generated, you MUST output a corresponding 'import' block.
                You must extract the correct identifying string from the input JSON based on this strict mapping matrix:

                1. aws_vpc -> Use the 'VpcId' (e.g., vpc-12345)
                2. aws_subnet -> Use the 'SubnetId' (e.g., subnet-67890)
                3. aws_security_group -> Use the 'GroupId' (e.g., sg-11111)
                4. aws_instance -> Use the 'InstanceId' (e.g., i-22222)
                5. aws_db_instance -> Use the DB 'DBInstanceIdentifier' name, NOT the ARN.
                6. aws_db_subnet_group -> Use the 'DBSubnetGroupName' string.

                Format the import block exactly like this at the top of the file:
                import {{
                  to = resource_type.local_name
                  id = "EXACT_MAPPED_STRING"
                }}
                CRITICAL: For aws_iam_role, the import `id` MUST be the Role Name (e.g., "my-role-name"), NOT the full ARN.
                NO DEFAULT TAGS: Do NOT add any default tags (like Environment, Owner, ManagedBy) if they are not explicitly present in the input JSON tags. ONLY copy the exact tags provided in the JSON telemetry. If the telemetry tags are empty, do NOT output a tags block at all.
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

    security_constraint = """
    CONSTRAINT: You are strictly responsible for Security. Only generate `resource` and `import` blocks for IAM roles, IAM policies, and Security Groups. NEVER generate `import` or `resource` blocks for VPCs or Subnets, even if they appear in your input JSON.
    """
    prompt = mode_instructions + "\n" + security_constraint + "\n" + SECURITY_PROMPT

    if mode == "new":
        prompt_user = state.get("user_prompt") + "\n\nABSOLUTE MANDATE FOR NEW MODE:\n1. NO VARIABLES ALLOWED: You are strictly FORBIDDEN from using ANY var.* references. You MUST hardcode ALL values. Hardcode cidr_blocks (e.g., '10.0.0.0/16') and tags (e.g., Environment = 'production').\n2. DEPENDENCIES & NAMING: You must strictly align resource names across files. The Network node MUST declare the VPC as 'resource \"aws_vpc\" \"main\"'. The Security node MUST declare the security group as 'resource \"aws_security_group\" \"main\"'. All cross-references must use 'aws_vpc.main.id' and 'aws_security_group.main.id'.\n3. BLOCK SYNTAX: Never use equals signs for repeatable configuration sub-blocks. Use 'attribute { ... }' instead of 'attribute = [ ... ]', and 'ingress { ... }' instead of 'ingress = [ ... ]'.\n4. DYNAMODB SYNTAX: If you generate an aws_dynamodb_table, you must define the 'hash_key'. If you specify 'read_capacity_units' and 'write_capacity_units', you MUST explicitly set 'billing_mode = \"PROVISIONED\"'.\n5. AWS_EIP SYNTAX: For aws_eip, you MUST ONLY use 'domain = \"vpc\"'. Completely remove 'vpc = true'."
    elif mode == "import":
        prompt_user = (
            "ABSOLUTE MANDATE FOR IMPORT MODE:\n"
            "1. SCOPE: ONLY generate resources explicitly listed in aws_input_data. If the JSON only has an S3 bucket, generate ONLY an aws_s3_bucket. DO NOT generate aws_db_instance or aws_autoscaling_group unless they are in the JSON.\n"
            "2. NO REFERENCES: NEVER use Terraform cross-references. WRONG: subnet_id = aws_subnet.sub-123.id. RIGHT: subnet_id = \"subnet-123\". MUST use string literals with quotes.\n"
            "3. NO VARIABLES: NEVER use var.* syntax. WRONG: username = var.user. RIGHT: username = \"admin\". Hardcode all values.\n"
            "4. IAM ROLES: When generating an aws_iam_role, you MUST define the required 'assume_role_policy' using a standard EC2 service trust policy document inside jsonencode.\n"
            "5. CRITICAL IMPORT BLOCK RULES:\n"
            "   For every resource generated, you MUST output a corresponding 'import' block.\n"
            "   You must extract the correct identifying string from the input JSON based on this strict mapping matrix:\n"
            "   1. aws_vpc -> Use the 'VpcId' (e.g., vpc-12345)\n"
            "   2. aws_subnet -> Use the 'SubnetId' (e.g., subnet-67890)\n"
            "   3. aws_security_group -> Use the 'GroupId' (e.g., sg-11111)\n"
            "   4. aws_instance -> Use the 'InstanceId' (e.g., i-22222)\n"
            "   5. aws_db_instance -> Use the DB 'DBInstanceIdentifier' name, NOT the ARN.\n"
            "   6. aws_db_subnet_group -> Use the 'DBSubnetGroupName' string.\n\n"
            "   Format the import block exactly like this at the top of the file:\n"
            "   import {\n"
            "     to = resource_type.local_name\n"
            "     id = \"EXACT_MAPPED_STRING\"\n"
            "   }"
        )
    else:  # clone
        prompt_user = "ABSOLUTE MANDATE FOR CLONE MODE:\n1. PARAMETERIZATION: Replace hardcoded IDs and names from the aws_input_data JSON with var.* references.\n2. VARIABLE DECLARATION: You MUST explicitly output a 'variable \"...\" { default = \"...\" }' block with the original scanned value from the telemetry set as the default, for EVERY var.* reference you generate. Write these variable blocks AT THE VERY TOP of your output, inside the exact same HCL block as your resources. DO NOT omit them thinking they belong in a separate variables.tf file. YOU MUST WRITE THEM HERE.\nEXAMPLE REQUIRED OUTPUT:\nvariable \"vpc_id\" { default = \"vpc-12345\" }\nresource \"aws_security_group\" \"main\" { vpc_id = var.vpc_id }\n\n3. SYNTAX: Do NOT generate 'aws_vpc_gateway_attachment' resources. Associate Internet Gateways directly by setting the 'vpc_id' argument inside the 'aws_internet_gateway' block.\n4. DOMAIN RESTRICTION: You are the SECURITY node. You MUST ONLY generate security resources (aws_security_group, IAM). Completely IGNORE any subnets, instances, or S3 buckets in the JSON. CRITICAL: Because Security Groups require a VPC, you will likely parameterize the vpc_id. You MUST explicitly declare variable blocks with defaults (e.g. `variable \"vpc_id\" { default = \"...\" }`) at the top of your output alongside any other variables.\n5. SECURITY GROUP RULES: You MUST inspect the ingress and egress arrays in the security group telemetry. For each rule, generate an inline 'ingress' or 'egress' block inside the 'aws_security_group' resource. Map the 'from_port', 'to_port', 'protocol', and 'cidr_blocks'/'ipv6_cidr_blocks'/'security_groups' values accurately. Do not leave the Security Group empty.\n6. TAG DEFAULT VALUE FIDELITY: When parameterizing the 'Environment' or 'Owner' tags, set their default values to sensible production settings (e.g. environment = \"production\", owner = \"LangGraph-Agent\") rather than empty strings, if they are empty in the AWS telemetry."

    # If there are validation results from a previous run, prepend them
    val_errors = state.get("validation_results", "").replace("{", "{{").replace("}", "}}")
    if val_errors:
        prompt = val_errors + "\n" + prompt

    resources = aws_input.get("resources", [])
    if not resources:
        hcl = "# No security resources required."
    else:
        hcl_blocks = []
        for resource in resources:
            single_input = {
                "region": aws_input.get("region"),
                "vpc_id": aws_input.get("vpc_id"),
                "resources": [resource]
            }
            single_prompt = f"""
            You are a strict, literal Terraform translator.
            Generate the HCL block for THIS EXACT RESOURCE ONLY:
            Type: {resource.get('type')}
            ID/Name: {resource.get('id') or resource.get('name')}
            
            CRITICAL DIRECTIVES:
            1. ONLY output the HCL block for the single resource defined above.
            2. DO NOT synthesize any other resources unless it is this exact resource.
            3. You MUST use the exact resource identifier/name (cleaned to replace hyphens with underscores in local labels) as the Terraform resource block identifier. Do NOT name it 'main', 'public', or 'private'.
            4. In import blocks, the 'to' attribute MUST be exactly '{resource.get('type')}.{resource.get('id') or resource.get('name')}' (or cleaned label).
            """ + "\n" + mode_instructions + "\n" + security_constraint + "\n" + COMPLIANCE_RULES
            
            if val_errors:
                single_prompt = val_errors + "\n" + single_prompt
                
            block = call_cloud_llm(
                single_prompt,
                {
                    "aws_input_data": single_input,
                    "user_prompt": prompt_user,
                    "network_context": (
                        "An existing VPC named 'aws_vpc.main' and subnets 'aws_subnet.public_1' "
                        "and 'aws_subnet.private_1' are already declared. DO NOT rewrite them."
                    ),
                },
            )
            hcl_blocks.append(block)
        hcl = "\n\n".join(hcl_blocks)
        
    parse_and_write_files(hcl, phase_filename="security.tf")
    return {"security_hcl": hcl, "current_phase": "security"}


def generate_compute_node(state: GraphState) -> dict:
    print("[Node] Generating Compute Configuration...")
    mode = state.get("deployment_mode")
    aws_input = filter_aws_input_data(state.get("aws_input_data", {}), "compute")

    if mode == "import":
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
                CRITICAL IMPORT BLOCK RULES:
                For every resource generated, you MUST output a corresponding 'import' block.
                You must extract the correct identifying string from the input JSON based on this strict mapping matrix:

                1. aws_vpc -> Use the 'VpcId' (e.g., vpc-12345)
                2. aws_subnet -> Use the 'SubnetId' (e.g., subnet-67890)
                3. aws_security_group -> Use the 'GroupId' (e.g., sg-11111)
                4. aws_instance -> Use the 'InstanceId' (e.g., i-22222)
                5. aws_db_instance -> Use the DB 'DBInstanceIdentifier' name, NOT the ARN.
                6. aws_db_subnet_group -> Use the 'DBSubnetGroupName' string.

                Format the import block exactly like this at the top of the file:
                import {{
                  to = resource_type.local_name
                  id = "EXACT_MAPPED_STRING"
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

    compute_data_constraints = """
    CONSTRAINT 1: You are strictly responsible for Compute and Data resources. NEVER generate `import` or `resource` blocks for VPCs, Subnets, or IAM roles. If your resources require an IAM role or VPC reference, use the raw ARN/ID string provided in the JSON, or reference the outputs from the injected {security_context} and {network_context}.

    CONSTRAINT 2: Terraform resource labels must start with a letter. If the extracted AWS resource ID begins with a number (e.g., a UUID like 3a327099...), you MUST prepend a string like `mapping_` or `res_` to the local resource name (e.g., `resource "aws_lambda_event_source_mapping" "mapping_3a327099..."`).

    CONSTRAINT 4 (Naming): Always use underscores instead of hyphens for Terraform resource names. (e.g., use `aws_sqs_queue.task_queue`, NEVER `aws_sqs_queue.task-queue`).
    CONSTRAINT 5 (Imports): In an `import` block, the `to` attribute must only contain the resource type and name. NEVER prepend it with `resource.`. (e.g., use `to = aws_sqs_queue.main`, NEVER `to = resource.aws_sqs_queue.main`).
    
    CRITICAL ARCHITECTURAL CONSTRAINT:
    You must process EVERY resource provided in the routed payload. 
    If the payload contains multiple independent VPCs or environments (e.g., prod and staging), you must generate separate infrastructure trees for EACH environment. 
    Do NOT drop environments. Do NOT synthesize unrequested auxiliary resources like extra subnets or internet gateways unless they are explicitly present in the input JSON data.

    CRITICAL FEW-SHOT EXAMPLE:
    You must map the exact 'id' from the JSON to the Terraform resource name. You must write a separate block for every single item in the list. Do not add anything else.

    If the JSON is:
    [
      {"type": "aws_instance", "id": "server-web-prod", "subnet_id": "subnet-prod-a"},
      {"type": "aws_instance", "id": "server-web-staging", "subnet_id": "subnet-staging-a"}
    ]

    Your output MUST BE exactly:
    resource "aws_instance" "server_web_prod" {
      ami           = "ami-0c55b159cbfafe1f0"
      instance_type = "t3.micro"
      subnet_id     = "subnet-prod-a"
      tags = {
        Name = "server-web-prod"
      }
    }

    resource "aws_instance" "server_web_staging" {
      ami           = "ami-0c55b159cbfafe1f0"
      instance_type = "t3.micro"
      subnet_id     = "subnet-staging-a"
      tags = {
        Name = "server-web-staging"
      }
    }

    CRITICAL INSTRUCTION: You are currently failing to map the staging resources. 
    You MUST output a terraform resource block for:
    - vpc-core-prod AND vpc-core-staging
    - subnet-web-prod AND subnet-web-staging
    - server-web-prod AND server-web-staging

    If you output 'prod' without also outputting 'staging', your execution will be terminated. Do not summarize. Output the exact blocks.
    """
    prompt = mode_instructions + "\n" + compute_data_constraints + "\n" + COMPUTE_PROMPT

    if mode == "new":
        prompt_user = state.get("user_prompt") + "\n\nABSOLUTE MANDATE FOR NEW MODE:\n1. NO VARIABLES ALLOWED: You are strictly FORBIDDEN from using ANY var.* references. You MUST hardcode ALL values. Hardcode cidr_blocks (e.g., '10.0.0.0/16') and tags (e.g., Environment = 'production').\n2. DEPENDENCIES & NAMING: You must strictly align resource names across files. The Network node MUST declare the VPC as 'resource \"aws_vpc\" \"main\"'. The Security node MUST declare the security group as 'resource \"aws_security_group\" \"main\"'. All cross-references must use 'aws_vpc.main.id' and 'aws_security_group.main.id'.\n3. BLOCK SYNTAX: Never use equals signs for repeatable configuration sub-blocks. Use 'attribute { ... }' instead of 'attribute = [ ... ]', and 'ingress { ... }' instead of 'ingress = [ ... ]'.\n4. DYNAMODB SYNTAX: If you generate an aws_dynamodb_table, you must define the 'hash_key'. You MUST set 'billing_mode = \"PAY_PER_REQUEST\"'. Do NOT specify 'read_capacity_units' or 'write_capacity_units'.\n5. AWS_EIP SYNTAX: For aws_eip, you MUST ONLY use 'domain = \"vpc\"'. Completely remove 'vpc = true'."
    elif mode == "import":
        prompt_user = (
            "ABSOLUTE MANDATE FOR IMPORT MODE:\n"
            "1. SCOPE: ONLY generate resources explicitly listed in aws_input_data. If the JSON only has an S3 bucket, generate ONLY an aws_s3_bucket. DO NOT generate aws_db_instance or aws_autoscaling_group unless they are in the JSON.\n"
            "2. NO REFERENCES: NEVER use Terraform cross-references. WRONG: subnet_id = aws_subnet.sub-123.id. RIGHT: subnet_id = \"subnet-123\". MUST use string literals with quotes.\n"
            "3. NO VARIABLES: NEVER use var.* syntax. WRONG: username = var.user. RIGHT: username = \"admin\". Hardcode all values.\n"
            "4. DYNAMODB: If you generate an aws_dynamodb_table, you MUST set billing_mode = \"PAY_PER_REQUEST\" and you are strictly FORBIDDEN from specifying read_capacity_units or write_capacity_units.\n"
            "5. NO DEFAULT TAGS: Do NOT add any default tags (like Environment, Owner, ManagedBy) if they are not explicitly present in the input JSON tags. ONLY copy the exact tags provided in the JSON telemetry. If the telemetry tags are empty, do NOT output a tags block at all.\n"
            "6. CRITICAL IMPORT BLOCK RULES:\n"
            "   For every resource generated, you MUST output a corresponding 'import' block.\n"
            "   You must extract the correct identifying string from the input JSON based on this strict mapping matrix:\n"
            "   1. aws_vpc -> Use the 'VpcId' (e.g., vpc-12345)\n"
            "   2. aws_subnet -> Use the 'SubnetId' (e.g., subnet-67890)\n"
            "   3. aws_security_group -> Use the 'GroupId' (e.g., sg-11111)\n"
            "   4. aws_instance -> Use the 'InstanceId' (e.g., i-22222)\n"
            "   5. aws_db_instance -> Use the DB 'DBInstanceIdentifier' name, NOT the ARN.\n"
            "   6. aws_db_subnet_group -> Use the 'DBSubnetGroupName' string.\n\n"
            "   Format the import block exactly like this at the top of the file:\n"
            "   import {\n"
            "     to = resource_type.local_name\n"
            "     id = \"EXACT_MAPPED_STRING\"\n"
            "   }"
        )
    else:  # clone
        prompt_user = "ABSOLUTE MANDATE FOR CLONE MODE:\n1. PARAMETERIZATION: Replace hardcoded IDs and names from the aws_input_data JSON with var.* references.\n2. VARIABLE DECLARATION: You MUST explicitly output a 'variable \"...\" { default = \"...\" }' block with the original scanned value from the telemetry set as the default, for EVERY var.* reference you generate. Write these variable blocks AT THE VERY TOP of your output, inside the exact same HCL block as your resources. DO NOT omit them thinking they belong in a separate variables.tf file. YOU MUST WRITE THEM HERE.\nEXAMPLE REQUIRED OUTPUT:\nvariable \"instance_type\" { default = \"t3.micro\" }\nresource \"aws_instance\" \"app\" { instance_type = var.instance_type }\n\n3. SYNTAX: Do NOT generate 'aws_vpc_gateway_attachment' resources. Associate Internet Gateways directly by setting the 'vpc_id' argument inside the 'aws_internet_gateway' block.\n4. DOMAIN RESTRICTION: You are the COMPUTE node. You MUST ONLY generate compute resources (aws_instance, ASG, Launch Templates). Completely IGNORE any subnets, security groups, or S3 buckets in the JSON. NEVER generate aws_subnet. CRITICAL: When you parameterize your resources, you MUST explicitly declare variables with defaults (e.g. `variable \"ami_id\" { default = \"...\" }`, `variable \"instance_type\" { default = \"...\" }`, and `variable \"subnet_id\" { default = \"...\" }`) at the top of your output alongside your resources.\n5. LAUNCH TEMPLATES & BOOTSTRAPPING: When generating an aws_launch_template, you MUST inspect its telemetry fields. If 'user_data' is present, set the 'user_data' argument. If 'block_device_mappings' are present, generate the matching nested 'block_device_mappings' blocks specifying device name, EBS volume size, and type. If 'iam_instance_profile' is present, specify it inside the launch template.\n6. TAG DEFAULT VALUE FIDELITY: When parameterizing the 'Environment' or 'Owner' tags, set their default values to sensible production settings (e.g. environment = \"production\", owner = \"LangGraph-Agent\") rather than empty strings, if they are empty in the AWS telemetry."

    # If there are validation results from a previous run, prepend them
    val_errors = state.get("validation_results", "").replace("{", "{{").replace("}", "}}")
    if val_errors:
        prompt = val_errors + "\n" + prompt

    resources = aws_input.get("resources", [])
    if not resources:
        hcl = "# No compute resources required."
    else:
        hcl_blocks = []
        for resource in resources:
            single_input = {
                "region": aws_input.get("region"),
                "vpc_id": aws_input.get("vpc_id"),
                "resources": [resource]
            }
            single_prompt = f"""
            You are a strict, literal Terraform translator.
            Generate the HCL block for THIS EXACT RESOURCE ONLY:
            Type: {resource.get('type')}
            ID/Name: {resource.get('id') or resource.get('name')}
            
            CRITICAL DIRECTIVES:
            1. ONLY output the HCL block for the single resource defined above.
            2. DO NOT synthesize any other resources unless it is this exact resource.
            3. You MUST use the exact resource identifier/name (cleaned to replace hyphens with underscores in local labels) as the Terraform resource block identifier. Do NOT name it 'main', 'public', or 'private'.
            4. In import blocks, the 'to' attribute MUST be exactly '{resource.get('type')}.{resource.get('id') or resource.get('name')}' (or cleaned label).
            """ + "\n" + mode_instructions + "\n" + compute_data_constraints + "\n" + COMPLIANCE_RULES
            
            if val_errors:
                single_prompt = val_errors + "\n" + single_prompt
                
            block = call_cloud_llm(
                single_prompt,
                {
                    "aws_input_data": single_input,
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
            hcl_blocks.append(block)
        hcl = "\n\n".join(hcl_blocks)
        
    parse_and_write_files(hcl, phase_filename="compute.tf")
    return {"compute_hcl": hcl, "current_phase": "compute"}


def generate_data_node(state: GraphState) -> dict:
    print("[Node] Generating Data Configuration...")
    mode = state.get("deployment_mode")
    aws_input = filter_aws_input_data(state.get("aws_input_data", {}), "data")

    if mode == "import":
        resources = aws_input.get("resources", [])
        has_data = any(r.get("type") in ["aws_s3_bucket", "aws_db_instance", "aws_dynamodb_table", "aws_sqs_queue", "aws_lambda_function", "aws_lambda_event_source_mapping"] for r in resources)
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
                CRITICAL IMPORT BLOCK RULES:
                For every resource generated, you MUST output a corresponding 'import' block.
                You must extract the correct identifying string from the input JSON based on this strict mapping matrix:

                1. aws_vpc -> Use the 'VpcId' (e.g., vpc-12345)
                2. aws_subnet -> Use the 'SubnetId' (e.g., subnet-67890)
                3. aws_security_group -> Use the 'GroupId' (e.g., sg-11111)
                4. aws_instance -> Use the 'InstanceId' (e.g., i-22222)
                5. aws_db_instance -> Use the DB 'DBInstanceIdentifier' name, NOT the ARN.
                6. aws_db_subnet_group -> Use the 'DBSubnetGroupName' string.

                Format the import block exactly like this at the top of the file:
                import {{
                  to = resource_type.local_name
                  id = "EXACT_MAPPED_STRING"
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

    compute_data_constraints = """
    CONSTRAINT 1: You are strictly responsible for Compute and Data resources. NEVER generate `import` or `resource` blocks for VPCs, Subnets, or IAM roles. If your resources require an IAM role or VPC reference, use the raw ARN/ID string provided in the JSON, or reference the outputs from the injected {security_context} and {network_context}.

    CONSTRAINT 2: Terraform resource labels must start with a letter. If the extracted AWS resource ID begins with a number (e.g., a UUID like 3a327099...), you MUST prepend a string like `mapping_` or `res_` to the local resource name (e.g., `resource "aws_lambda_event_source_mapping" "mapping_3a327099..."`).

    CONSTRAINT 3 (DynamoDB): When generating an `aws_dynamodb_table` resource, DO NOT use the `attribute_definitions` list from the JSON input. You must convert it into individual Terraform `attribute {}` blocks. 
    For example, instead of `attribute_definitions = [{name = "id", type = "S"}]`, you MUST write:
    attribute {
      name = "id"
      type = "S"
    }

    CONSTRAINT 4 (Naming): Always use underscores instead of hyphens for Terraform resource names. (e.g., use `aws_sqs_queue.task_queue`, NEVER `aws_sqs_queue.task-queue`).
    CONSTRAINT 5 (Imports): In an `import` block, the `to` attribute must only contain the resource type and name. NEVER prepend it with `resource.`. (e.g., use `to = aws_sqs_queue.main`, NEVER `to = resource.aws_sqs_queue.main`).
    """
    prompt = mode_instructions + "\n" + compute_data_constraints + "\n" + DATA_PROMPT

    if mode == "new":
        prompt_user = state.get("user_prompt") + "\n\nABSOLUTE MANDATE FOR NEW MODE:\n1. NO VARIABLES ALLOWED: You are strictly FORBIDDEN from using ANY var.* references. You MUST hardcode ALL values. Hardcode cidr_blocks (e.g., '10.0.0.0/16') and tags (e.g., Environment = 'production').\n2. DEPENDENCIES & NAMING: You must strictly align resource names across files. The Network node MUST declare the VPC as 'resource \"aws_vpc\" \"main\"'. The Security node MUST declare the security group as 'resource \"aws_security_group\" \"main\"'. All cross-references must use 'aws_vpc.main.id' and 'aws_security_group.main.id'.\n3. BLOCK SYNTAX: Never use equals signs for repeatable configuration sub-blocks. Use 'attribute { ... }' instead of 'attribute = [ ... ]', and 'ingress { ... }' instead of 'ingress = [ ... ]'.\n4. DYNAMODB SYNTAX: If you generate an aws_dynamodb_table, you must define the 'hash_key'. You MUST set 'billing_mode = \"PAY_PER_REQUEST\"'. Do NOT specify 'read_capacity_units' or 'write_capacity_units'.\n5. AWS_EIP SYNTAX: For aws_eip, you MUST ONLY use 'domain = \"vpc\"'. Completely remove 'vpc = true'.\n6. RDS SUBNETS: Never place subnet_ids directly inside an aws_db_instance. When network subnets are provided for a database, always generate a separate aws_db_subnet_group resource and link it to the aws_db_instance using the db_subnet_group_name attribute. Never use the name \"default\" for an aws_db_subnet_group. Always generate a descriptive name based on the environment or database identifier (e.g., \"main-db-subnet-group\"). When creating an aws_db_subnet_group, you must only populate the subnet_ids array using the exact resource addresses/IDs of aws_subnet resources that already exist in the network state. Do not invent new subnet IDs."
    elif mode == "import":
        prompt_user = (
            "ABSOLUTE MANDATE FOR IMPORT MODE:\n"
            "1. SCOPE: ONLY generate resources explicitly listed in aws_input_data. If the JSON only has an S3 bucket, generate ONLY an aws_s3_bucket. DO NOT generate aws_db_instance or aws_autoscaling_group unless they are in the JSON.\n"
            "2. NO REFERENCES: NEVER use Terraform cross-references. WRONG: subnet_id = aws_subnet.sub-123.id. RIGHT: subnet_id = \"subnet-123\". MUST use string literals with quotes.\n"
            "3. NO VARIABLES: NEVER use var.* syntax. WRONG: username = var.user. RIGHT: username = \"admin\". Hardcode all values.\n"
            "4. DYNAMODB: If you generate an aws_dynamodb_table, you MUST set billing_mode = \"PAY_PER_REQUEST\" and you are strictly FORBIDDEN from specifying read_capacity_units or write_capacity_units.\n"
            "5. RDS INSTANCES: When generating an aws_db_instance, if 'storage_encrypted = true' is in the telemetry, you MUST explicitly set 'storage_encrypted = true' in the resource block. Never place subnet_ids directly inside an aws_db_instance resource block. When network subnets are provided for a database, you MUST generate a separate aws_db_subnet_group resource and link it using the db_subnet_group_name attribute. Never use the name \"default\" for an aws_db_subnet_group. Always generate a descriptive name based on the environment or database identifier (e.g., \"main-db-subnet-group\"). When creating an aws_db_subnet_group, you must only populate the subnet_ids array using the exact IDs of aws_subnet resources that already exist in the provided network state. Do not invent new subnet IDs.\n"
            "6. NO DEFAULT TAGS: Do NOT add any default tags (like Environment, Owner, ManagedBy) if they are not explicitly present in the input JSON tags. ONLY copy the exact tags provided in the JSON telemetry. If the telemetry tags are empty, do NOT output a tags block at all.\n"
            "7. CRITICAL IMPORT BLOCK RULES:\n"
            "   For every resource generated, you MUST output a corresponding 'import' block.\n"
            "   You must extract the correct identifying string from the input JSON based on this strict mapping matrix:\n"
            "   1. aws_vpc -> Use the 'VpcId' (e.g., vpc-12345)\n"
            "   2. aws_subnet -> Use the 'SubnetId' (e.g., subnet-67890)\n"
            "   3. aws_security_group -> Use the 'GroupId' (e.g., sg-11111)\n"
            "   4. aws_instance -> Use the 'InstanceId' (e.g., i-22222)\n"
            "   5. aws_db_instance -> Use the DB 'DBInstanceIdentifier' name, NOT the ARN.\n"
            "   6. aws_db_subnet_group -> Use the 'DBSubnetGroupName' string.\n\n"
            "   Format the import block exactly like this at the top of the file:\n"
            "   import {\n"
            "     to = resource_type.local_name\n"
            "     id = \"EXACT_MAPPED_STRING\"\n"
            "   }"
        )
    else:  # clone
        prompt_user = "ABSOLUTE MANDATE FOR CLONE MODE:\n1. PARAMETERIZATION: Replace hardcoded IDs and names from the aws_input_data JSON with var.* references.\n2. VARIABLE DECLARATION: You MUST explicitly output a 'variable \"...\" { default = \"...\" }' block with the original scanned value from the telemetry set as the default, for EVERY var.* reference you generate. Write these variable blocks AT THE VERY TOP of your output, inside the exact same HCL block as your resources. DO NOT omit them thinking they belong in a separate variables.tf file. YOU MUST WRITE THEM HERE.\nEXAMPLE REQUIRED OUTPUT:\nvariable \"s3_bucket_name\" { default = \"my-scanned-bucket\" }\nresource \"aws_s3_bucket\" \"main\" { bucket = var.s3_bucket_name }\n\n3. SYNTAX: Do NOT generate 'aws_vpc_gateway_attachment' resources. Associate Internet Gateways directly by setting the 'vpc_id' argument inside the 'aws_internet_gateway' block.\n4. DOMAIN RESTRICTION: You are the DATA node. You MUST ONLY generate data/storage resources (aws_s3_bucket, RDS). Completely IGNORE any subnets, instances, or security groups in the JSON. NEVER generate aws_subnet or aws_security_group.\n5. S3 & DYNAMODB CONFIG: When generating an aws_s3_bucket, if the telemetry contains 'versioning' settings, you are strictly FORBIDDEN from nesting a versioning block inside aws_s3_bucket. Instead, you MUST generate a separate, dedicated aws_s3_bucket_versioning resource block (e.g., resource \"aws_s3_bucket_versioning\" \"...\" { bucket = aws_s3_bucket.main.id ... }). If the telemetry contains 'server_side_encryption' settings, map them using nested blocks (e.g. `server_side_encryption_configuration`). When generating an aws_dynamodb_table, you MUST inspect the 'attribute_definitions' and 'hash_key'/'range_key' lists in the telemetry and define the 'attribute' block and keys matching them exactly.\n6. TAG DEFAULT VALUE FIDELITY: When parameterizing the 'Environment' or 'Owner' tags, set their default values to sensible production settings (e.g. environment = \"production\", owner = \"LangGraph-Agent\") rather than empty strings, if they are empty in the AWS telemetry.\n7. DATABASE SYNTAX: Never place subnet_ids directly inside an aws_db_instance. When network subnets are provided for a database, always generate a separate aws_db_subnet_group resource and link it to the aws_db_instance using the db_subnet_group_name attribute. Never use the name \"default\" for an aws_db_subnet_group. Always generate a descriptive name based on the environment or database identifier (e.g., \"main-db-subnet-group\"). When creating an aws_db_subnet_group, you must only populate the subnet_ids array using the exact resource addresses/IDs of aws_subnet resources that already exist in the provided network state. Do not invent new subnet IDs."

    val_errors = state.get("validation_results", "").replace("{", "{{").replace("}", "}}")
    if val_errors:
        prompt = val_errors + "\n" + prompt

    resources = aws_input.get("resources", [])
    if not resources:
        hcl = "# No data resources required."
    else:
        hcl_blocks = []
        for resource in resources:
            single_input = {
                "region": aws_input.get("region"),
                "vpc_id": aws_input.get("vpc_id"),
                "resources": [resource]
            }
            single_prompt = f"""
            You are a strict, literal Terraform translator.
            Generate the HCL block for THIS EXACT RESOURCE ONLY:
            Type: {resource.get('type')}
            ID/Name: {resource.get('id') or resource.get('name')}
            
            CRITICAL DIRECTIVES:
            1. ONLY output the HCL block for the single resource defined above.
            2. DO NOT synthesize any other resources unless it is this exact resource.
            3. You MUST use the exact resource identifier/name (cleaned to replace hyphens with underscores in local labels) as the Terraform resource block identifier. Do NOT name it 'main', 'public', or 'private'.
            4. In import blocks, the 'to' attribute MUST be exactly '{resource.get('type')}.{resource.get('id') or resource.get('name')}' (or cleaned label).
            """ + "\n" + mode_instructions + "\n" + compute_data_constraints + "\n" + COMPLIANCE_RULES
            
            if val_errors:
                single_prompt = val_errors + "\n" + single_prompt
                
            block = call_cloud_llm(
                single_prompt,
                {
                    "aws_input_data": single_input,
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
            hcl_blocks.append(block)
        hcl = "\n\n".join(hcl_blocks)
        
    parse_and_write_files(hcl, phase_filename="data.tf")
    return {"data_hcl": hcl, "current_phase": "data"}


# Validation node
SKIP_VALIDATE = ("--skip-validate" in sys.argv) or (
    os.environ.get("SKIP_VALIDATE") == "1"
)


def validation_node_func(state: GraphState) -> dict:
    print("[Node] Running Validation...")
    
    # Scrub variables on disk to consolidate duplicates before running validation
    scrub_workspace_variables("terraform_workspace")

    workspace_dir = "terraform_workspace"
    os.makedirs(workspace_dir, exist_ok=True)
    
    # 1. AUTOMATION STEP: Overwrite provider.tf with a local-safe dummy provider
    mock_provider_hcl = """provider "aws" {
  region                      = "us-east-1"
  access_key                  = "mock_access_key"
  secret_key                  = "mock_secret_key"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
}
"""
    provider_file_path = os.path.join(workspace_dir, "provider.tf")
    try:
        with open(provider_file_path, "w", encoding="utf-8") as f:
            f.write(mock_provider_hcl)
    except Exception as e:
        print(f"[Validator] Error writing mock provider.tf: {str(e)}")

    # 1. SCRUBBER STEP: Forcefully remove LLM S3 hallucinations before validation
    print("[Node] Scrubbing deprecated S3 syntax from generated files...")
    from src.utils import scrub_deprecated_s3_syntax
    scrub_deprecated_s3_syntax(workspace_dir)

    # Compile the infrastructure graph
    from src.aws_client import compile_infrastructure_graph
    
    mode = state.get("deployment_mode")
    graph = {"nodes": {}, "edges": []}
    try:
        if mode == "import":
            raw_data = state.get("aws_input_data", {})
            graph = compile_infrastructure_graph(raw_data, mode)
        elif mode in ["clone", "new"]:
            # Read generated HCL files from workspace
            workspace_dir = "terraform_workspace"
            hcl_parts = []
            if os.path.exists(workspace_dir):
                for filename in os.listdir(workspace_dir):
                    if filename.endswith(".tf"):
                        with open(os.path.join(workspace_dir, filename), "r", encoding="utf-8") as f:
                            hcl_parts.append(f.read())
            combined_hcl = "\n".join(hcl_parts)
            # Use 'clone' parser format for HCL strings
            graph = compile_infrastructure_graph(combined_hcl, "clone")
    except Exception as e:
        print(f"[Validator] Error compiling infrastructure graph: {str(e)}")

    validation_success = False
    validation_result = {}
    if SKIP_VALIDATE:
        print("[Validator] SKIP_VALIDATE enabled; forcing success (dry-run).")
        validation_success = True
    else:
        validation_result = execute_terraform_validation()
        if validation_result.get("is_valid"):
            validation_success = True

    if validation_success:
        print("[Node] Validation Passed. Compiling and rendering topology graphs...")
        state["infrastructure_graph"] = graph
        try:
            from src.utils import generate_png_graph, generate_drawio_xml
            png_path = generate_png_graph(state, workspace_dir=workspace_dir)
            drawio_path = generate_drawio_xml(state, workspace_dir=workspace_dir)
            print(f"[Success] Generated visual assets:\n - {png_path}\n - {drawio_path}")
        except Exception as e:
            print(f"[Warning] Graph rendering failed: {str(e)}")
        
        return {"is_valid": True, "infrastructure_graph": graph}
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
            "infrastructure_graph": graph,
        }




