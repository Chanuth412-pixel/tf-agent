import re
import os
import subprocess
import json
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from config.settings import OLLAMA_BASE_URL, MODEL_NAME, NUM_CTX, DEBUG


"""Utility helpers: prompts, LLM client, and HCL validation helpers."""

# -------------------- Prompts (migrated) --------------------
COMMON_RULES = """
CRITICAL RULES (apply to all phases):
1. Output ONLY valid HCL (no markdown, no prose).
2. Do NOT hardcode tunables: use variables from `variables.tf` (e.g., var.vpc_cidr, var.instance_type, var.ami_id, var.db_name).
3. Every resource must include a `tags` block with at least: Environment, Owner, ManagedBy = "LangGraph-Agent".
4. Reference upstream resources by resource address (e.g., `aws_vpc.main.id`, `aws_subnet.private_1.id`).
5. Add a short `description` argument on resources when applicable.
6. Keep blocks consistently spaced and group related resources.
"""

NETWORK_PROMPT = f"""
{COMMON_RULES}

Generate the NETWORK layer only. Produce resources for VPC, public and
private subnets, internet gateway, and route tables. Use variables for CIDR
values (e.g., var.vpc_cidr, var.public_subnet_cidr, var.private_subnet_cidr).
Name the VPC `aws_vpc.main` and subnets `aws_subnet.public_1` and
`aws_subnet.private_1` so downstream phases can reference them.

CRITICAL SYNTAX RULES:
1. When defining route tables, use the exact singular block `route {{{{...}}}}` (do NOT use `routes`).
2. For public internet access routes, set the `cidr_block` explicitly to "0.0.0.0/0".
3. Declare variables and locals only in `variables.tf` or inside a single `locals {{{{...}}}}` block — do NOT emit naked assignments at top-level.
4. Ensure resource names are stable and deterministic (e.g., `aws_vpc.main`, `aws_subnet.public_1`).
"""

SECURITY_PROMPT = f"""
{COMMON_RULES}

Generate the SECURITY layer only: security groups, network ACLs, IAM roles
and policies. Use the provided `network_context` to reference `aws_vpc.main.id`
and subnet resources. Ensure security groups reference `aws_vpc.main.id` and
attach the standard `tags` block using `var.environment` and `var.owner`.

CRITICAL SYNTAX RULES:
1. Do NOT redefine or redeclare the `resource "aws_vpc" "main"` block — it must exist only in `network.tf`.
2. Always reference the VPC using the exact attribute `aws_vpc.main.id`.
3. Do NOT emit naked top-level assignments; if local values are required wrap them inside `locals {{{{...}}}}`.
4. Avoid duplicating security group names between runs; use fixed resource addressing.

CRITICAL SECURITY GROUP HCL RULES:
1. NEVER use the list assignment syntax (`ingress = [{{...}}]` or `egress = [{{...}}]`) for security group rules.
2. You MUST use the block syntax without the equals sign or brackets: 
   ingress {{
     from_port   = 80
     to_port     = 80
     protocol    = "tcp"
     cidr_blocks = ["0.0.0.0/0"]
   }}
3. Do not include optional attributes like `ipv6_cidr_blocks`, `prefix_list_ids`, or `security_groups` unless they are explicitly provided in the source data.
"""

COMPUTE_PROMPT = f"""
{COMMON_RULES}

Generate the COMPUTE layer only: EC2 instances, launch templates, and
auto-scaling groups. Reference `aws_subnet.public_1.id` or `aws_subnet.private_1.id`
as appropriate and reference security groups by resource address. Use
`var.instance_type` and `var.ami_id` rather than hardcoding values.
Include placements across subnets as necessary.

CRITICAL SYNTAX RULES:
1. Do NOT redefine the VPC or any Security Groups — reference them by resource address only.
2. Attach instances to subnets using `subnet_id = aws_subnet.public_1.id` (do not hardcode strings).
3. Do NOT reference resources that are not declared (e.g., `aws_key_pair`), unless explicitly created within this compute phase.
"""

DATA_PROMPT = f"""
{COMMON_RULES}

Generate the DATA layer only: RDS instances, DB subnet groups, and S3
buckets. Place any databases in private subnets and reference security groups
from the security phase. Use `var.db_name`, `var.db_username`, `var.db_password`
via variables (avoid plaintext credentials in HCL files; allow variables to
be set externally).

CRITICAL AWS PROVIDER V5 RULES:
1. NEVER put 'versioning', 'server_side_encryption', or 'acl' inside the 'aws_s3_bucket' block.
2. If you need versioning, create a SEPARATE resource: 'aws_s3_bucket_versioning'.
3. If you need encryption, create a SEPARATE resource: 'aws_s3_bucket_server_side_encryption_configuration'.
4. NEVER place 'subnet_ids' directly inside an 'aws_db_instance' resource block. When network subnets are provided for a database, always generate a separate 'aws_db_subnet_group' resource and link it to the database instance using the 'db_subnet_group_name' attribute. Never use the name "default" for an aws_db_subnet_group. Always generate a descriptive name based on the environment or database identifier (e.g., "main-db-subnet-group"). When creating an aws_db_subnet_group, you must only populate the subnet_ids array using the exact resource addresses/IDs of aws_subnet resources that already exist in the provided network state. Do not invent new subnet IDs.

CRITICAL DYNAMODB HCL RULES:
1. NEVER use `key_schema` blocks inside `aws_dynamodb_table`. This is an unsupported block type.
2. You MUST define primary keys using root-level arguments: `hash_key = "AttributeName"` and `range_key = "AttributeName"`.
If you violate these rules, the system will crash.
"""

COMPLIANCE_RULES = """
========================================================================
CRITICAL SYNTAX & ARCHITECTURAL COMPLIANCE RULES:
CRITICAL CONSTRAINT: Return ONLY valid, raw HCL structural syntax code blocks. Do NOT wrap your output in markdown syntax tags (such as ```hcl). Do NOT include any conversational introduction, explanation, or notes. Your response must start immediately with the resource definition and contain nothing else.
- OUTPUT RAW HCL ONLY. Do NOT use markdown code blocks (```hcl ... ```).
- DO NOT declare 'variable {{}}' blocks. All variables are pre-defined in variables.tf.
- DO NOT declare 'provider {{}}' or 'terraform {{}}' blocks. They live in provider.tf.
- DO NOT re-declare or copy resource blocks from previous steps (e.g., Do NOT declare 'resource "aws_vpc" "main"' outside of the network phase).
- Use exact AWS resource keys: Use 'ami' (NOT 'ami_id'), Use 'aws_db_instance' (NOT 'aws_rds_instance'), and do NOT place 'acl = "private"' inside aws_s3_bucket.
- For `aws_subnet`: Always use **`availability_zone`** (NEVER use `az`).
- For `aws_autoscaling_group`:
  1. `vpc_zone_identifier` MUST be a list/set of strings (e.g., `["subnet-123"]`, not `"subnet-123"`).
  2. STRICTLY FORBIDDEN: You are strictly forbidden from writing a `tags` (e.g. `tags = [...]`) or `tags_all` attribute block inside `aws_autoscaling_group`. You MUST define every tag using a separate `tag {{ key = "..." value = "..." propagate_at_launch = true }}` block.
  3. You MUST always specify one of `launch_configuration`, `launch_template`, or `mixed_instances_policy` (e.g., `launch_template {{ id = "..." }}`). If none is specified in the telemetry, reference a placeholder launch template block.
- For `aws_dynamodb_table`: You MUST set `billing_mode = "PAY_PER_REQUEST"`. You are strictly FORBIDDEN from specifying `read_capacity_units` or `write_capacity_units`.
- For `aws_iam_role`: You MUST always specify the required **`assume_role_policy`** argument. If the exact policy document is not provided in the AWS telemetry, default to a standard EC2 service assume-role policy trust document via `jsonencode`.
- For Terraform 1.5+ `import` blocks: You MUST always specify the `to` and `id` arguments. The argument `id` is REQUIRED and must be named exactly `id` (e.g., `id = "..."`). You are strictly FORBIDDEN from using `name = "..."` or any other argument name in place of `id`.
  CRITICAL: All `import` blocks MUST be top-level blocks outside and separate from any resource blocks. You are strictly FORBIDDEN from nesting `import` blocks inside a resource block body.
- For all resources (especially `aws_security_group` description and resource `tags` blocks): You MUST inspect the input JSON telemetry. If a resource contains a `description` or a `tags` block, you MUST copy the description value and the tag key-value pairs EXACTLY as they are into the generated HCL resource blocks. You are strictly FORBIDDEN from overriding, removing, or ignoring live tags (e.g. `Name` tags) or descriptions in favor of generic defaults (like Environment, Owner, ManagedBy) unless the telemetry tags are empty.
- For `aws_s3_bucket`: The argument to set the bucket name is `bucket` (e.g., `bucket = "my-bucket"`). You are strictly FORBIDDEN from using `name = "my-bucket"`. You are strictly FORBIDDEN from nesting a `versioning { ... }` block inside the `aws_s3_bucket` resource block. Instead, bucket versioning must always be declared as a separate, dedicated `aws_s3_bucket_versioning` resource.
  Example:
  resource "aws_s3_bucket" "example" {
    bucket = "my-bucket"
  }
  resource "aws_s3_bucket_versioning" "example_versioning" {
    bucket = aws_s3_bucket.example.id
    versioning_configuration {
      status = "Enabled"
    }
  }
- For resource local names (the block identifier after the resource type, e.g. `resource "aws_s3_bucket" "local_name"`): You MUST replace any hyphens (`-`) in the AWS resource name with underscores (`_`) (e.g. use `tf_engine_state_table` instead of `tf-engine-state-table` for the block identifier). However, the actual resource argument fields (like `name = "..."`, `bucket = "..."`, and `id = "..."` inside the `import` block) MUST keep their original hyphens to match AWS exactly.
- Do NOT add a `description` argument to resources unless it is explicitly supported by that resource type (e.g. `aws_security_group` supports it, but `aws_autoscaling_group` and `aws_subnet` do NOT).
- For `aws_db_instance`: NEVER place the `subnet_ids` argument directly inside the resource block. You are strictly FORBIDDEN from putting a list of subnet IDs inside `aws_db_instance`. Instead, you MUST create a separate `aws_db_subnet_group` resource specifying those subnets in its `subnet_ids` field, and link the `aws_db_instance` to the subnet group by setting `db_subnet_group_name = aws_db_subnet_group.<name>.name`.
========================================================================
"""

# Append compliance rules to each prompt to enforce strict boundaries
NETWORK_PROMPT = NETWORK_PROMPT + "\n" + COMPLIANCE_RULES
SECURITY_PROMPT = SECURITY_PROMPT + "\n" + COMPLIANCE_RULES
COMPUTE_PROMPT = COMPUTE_PROMPT + "\n" + COMPLIANCE_RULES
DATA_PROMPT = DATA_PROMPT + "\n" + COMPLIANCE_RULES


# -------------------- LLM client (migrated) --------------------
def call_cloud_llm(prompt_template: str, input_variables: dict) -> str:
    llm = ChatOllama(
        model=MODEL_NAME.strip(),
        temperature=0.0,
        base_url=OLLAMA_BASE_URL.strip(),
        num_ctx=NUM_CTX,
    )
    # Strict base instruction to prevent conversational text or titles
    BASE_SYSTEM_INSTRUCTION = """
You are an expert Terraform engineer.

CRITICAL INSTRUCTION: You must output ONLY valid Terraform HCL wrapped in ```hcl``` blocks.
DO NOT output any conversational text.
DO NOT add titles like "Network Layer:" or "Data layer:."
DO NOT add explanations.
Your entire output must be parseable by the `terraform fmt` command.

{aws_input_data}
{user_prompt}
"""
    # Note: strict, mode-specific rules (variables, scope, dependencies) are
    # intentionally applied at the node level (in `src/nodes.py`) per-mode.
    # The base system instruction enforces only HCL-only output and no prose.

    full_prompt = BASE_SYSTEM_INSTRUCTION + "\n" + prompt_template
    
    # Escape all curly braces to avoid LangChain formatting issues
    escaped_prompt = full_prompt.replace("{", "{{").replace("}", "}}")
    
    # Restore only the placeholders that correspond to input_variables keys
    for key in input_variables.keys():
        escaped_prompt = escaped_prompt.replace(f"{{{{{key}}}}}", f"{{{key}}}")
        
    prompt = ChatPromptTemplate.from_template(escaped_prompt)
    chain = prompt | llm
    if DEBUG:
        print(f"    [LLM] Sending request to local Ollama ({MODEL_NAME}) with num_ctx={NUM_CTX}...")
    response = chain.invoke(input_variables)
    return response.content


# -------------------- Validator helpers (migrated) --------------------
def clean_hcl_output(text: str) -> str:
    if not text:
        return ""

    # 1) Prefer explicit triple-backtick HCL blocks (```hcl ... ``` or ``` ... ```)
    match = re.search(r"```(?:hcl)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if match:
        content = match.group(1)
    else:
        # 2) No fenced block found — remove stray leading language tags or single-word markers
        lines = text.splitlines()
        i = 0
        # Drop leading lines that are empty or only contain fence markers or a single language tag like 'hcl'
        while i < len(lines) and re.match(r'^\s*(?:```\w*|```|hcl|terraform|json|yaml)?\s*$', lines[i], re.IGNORECASE):
            i += 1
        content = "\n".join(lines[i:]).strip()

        # 3) Try to find the first real HCL construct if extra prose remains
        m = re.search(r"(?=resource\s+|locals\s*\{|data\s+|module\s+|provider\s+\{|variable\s+)", content)
        if m:
            content = content[m.start():].strip()

    # 4) Final cleanup: strip any remaining leading/trailing fence tokens or lone language tags
    # Remove any leading single-line language tags like 'hcl' or 'terraform'
    content = re.sub(r'^\s*(?:```\w*|```|hcl|terraform|json|yaml)\s*\n', '', content, flags=re.IGNORECASE)
    # Remove trailing fences
    content = re.sub(r'\n?```+\s*$', '', content, flags=re.IGNORECASE)
    # Remove stray inline 'hcl' tokens on their own lines
    content = re.sub(r'^\s*hcl\s*$', '', content, flags=re.IGNORECASE | re.MULTILINE)

    return content.strip()

def scrub_workspace_variables(workspace_dir: str = "terraform_workspace") -> None:
    """
    Scans every .tf file in the workspace directory (except provider.tf),
    extracts all variable blocks using robust brace-matching, deletes them from
    the source files, and writes them uniquely to variables.tf.
    """
    import os
    import re
    import glob
    
    if not os.path.exists(workspace_dir):
        return
        
    variables_map = {}
    tf_files = glob.glob(os.path.join(workspace_dir, "*.tf"))
    
    # 1. Read variables from all tf files
    for tf_file in tf_files:
        basename = os.path.basename(tf_file)
        if basename == "provider.tf":
            continue
            
        try:
            with open(tf_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue
            
        clean_content = content
        modified = False
        
        matches = list(re.finditer(r'variable\s+"([^"]+)"\s*\{', clean_content))
        for match in reversed(matches):
            var_name = match.group(1)
            start_idx = match.end() - 1
            brace_count = 0
            block_content = []
            has_ended = False
            for char in clean_content[start_idx:]:
                block_content.append(char)
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        has_ended = True
                        break
            
            if has_ended:
                block_str = "".join(block_content)
                full_block = f'variable "{var_name}" ' + block_str
                
                if var_name not in variables_map:
                    variables_map[var_name] = full_block
                    
                end_idx = start_idx + len(block_str)
                block_start = match.start()
                clean_content = clean_content[:block_start] + clean_content[end_idx:]
                modified = True
                
        if modified:
            try:
                with open(tf_file, "w", encoding="utf-8") as f:
                    f.write(clean_content.strip() + "\n")
            except Exception as e:
                print(f"Error writing scrubbed file {tf_file}: {e}")

    # 2. Write master variables.tf
    variables_tf_path = os.path.join(workspace_dir, "variables.tf")
    if variables_map:
        try:
            sorted_vars = sorted(variables_map.items())
            variables_tf_content = "\n\n".join(val for name, val in sorted_vars)
            with open(variables_tf_path, "w", encoding="utf-8") as f:
                f.write(variables_tf_content.strip() + "\n")
        except Exception as e:
            print(f"Error writing variables.tf: {e}")
    else:
        # If no variables remain, delete variables.tf if it exists
        if os.path.exists(variables_tf_path):
            try:
                os.remove(variables_tf_path)
            except Exception:
                pass


def consolidate_terraform_variables(hcl_files_dict):
    """
    Scans generated HCL strings, extracts all variable blocks, deduplicates them,
    and creates a unified variables.tf string while cleaning the source files.
    """
    cleaned_files = {}
    unique_variables = {}
    
    for filename, hcl_string in hcl_files_dict.items():
        if filename == "provider.tf":
            cleaned_files[filename] = hcl_string
            continue
            
        clean_hcl = hcl_string
        # Use regex to find variable declarations, then use brace matching to extract the entire block
        matches = list(re.finditer(r'variable\s+"([^"]+)"\s*\{', clean_hcl))
        
        # Process in reverse to delete without corrupting indices
        for match in reversed(matches):
            var_name = match.group(1)
            start_idx = match.end() - 1
            brace_count = 0
            block_content = []
            has_ended = False
            for char in clean_hcl[start_idx:]:
                block_content.append(char)
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        has_ended = True
                        break
            
            if has_ended:
                block_str = "".join(block_content)
                full_block = f'variable "{var_name}" ' + block_str
                
                # Save it if we haven't seen it yet
                if var_name not in unique_variables:
                    unique_variables[var_name] = full_block
                
                # Remove it from the current file string
                end_idx = start_idx + len(block_str)
                block_start = match.start()
                clean_hcl = clean_hcl[:block_start] + clean_hcl[end_idx:]
                
        cleaned_files[filename] = clean_hcl.strip()
        
    # Combine all unique variables into a single string (sorted alphabetically for clean output)
    sorted_vars = sorted(unique_variables.items())
    variables_tf_content = "\n\n".join(val for name, val in sorted_vars)
    
    return cleaned_files, variables_tf_content


def deduplicate_resources(workspace_dir: str) -> None:
    """
    Scans all .tf files in the workspace, finds duplicate resource definitions
    (same type and local name), and removes the duplicate blocks from subsequent files.
    """
    import re
    import glob
    
    seen_resources = set()
    tf_files = glob.glob(os.path.join(workspace_dir, "*.tf"))
    
    # Establish logical order to prioritize keeping resources in their proper phase files
    logical_order = ["network.tf", "security.tf", "compute.tf", "data.tf"]
    ordered_files = []
    
    for f_name in logical_order:
        full_path = os.path.join(workspace_dir, f_name)
        if full_path in tf_files:
            ordered_files.append(full_path)
            
    for tf_file in tf_files:
        if tf_file not in ordered_files and os.path.basename(tf_file) != "variables.tf":
            ordered_files.append(tf_file)

    for tf_file in ordered_files:
        if not os.path.exists(tf_file):
            continue
        try:
            with open(tf_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue

        modified = False
        new_content = content

        matches = list(re.finditer(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', new_content))
        for match in reversed(matches):
            res_type = match.group(1)
            res_name = match.group(2)
            resource_key = f"{res_type}.{res_name}"

            start_idx = match.end() - 1
            brace_count = 0
            block_content = []
            has_ended = False
            for char in new_content[start_idx:]:
                block_content.append(char)
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        has_ended = True
                        break

            if has_ended:
                block_str = "".join(block_content)
                if resource_key in seen_resources:
                    end_idx = start_idx + len(block_str)
                    block_start = match.start()
                    new_content = new_content[:block_start] + new_content[end_idx:]
                    modified = True
                    print(f"[Deduplicator] Removed duplicate resource definition: {resource_key} in {os.path.basename(tf_file)}")
                else:
                    seen_resources.add(resource_key)

        if modified:
            try:
                with open(tf_file, "w", encoding="utf-8") as f:
                    f.write(new_content.strip() + "\n")
            except Exception as e:
                print(f"Error writing deduplicated {tf_file}: {e}")


def parse_and_write_files(data, phase_filename=None):
    workspace_dir = "terraform_workspace"
    os.makedirs(workspace_dir, exist_ok=True)

    # 1. Gather all files in the workspace (read existing ones so we have the global context)
    all_files = {
        "network.tf": "",
        "security.tf": "",
        "compute.tf": "",
        "data.tf": "",
        "variables.tf": ""
    }
    
    # Read what's currently in the workspace
    for filename in all_files.keys():
        filepath = os.path.join(workspace_dir, filename)
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    all_files[filename] = f.read()
            except Exception:
                pass

    # 2. Update with the new generated HCL contents
    if isinstance(data, dict):
        if data.get("network_hcl"): all_files["network.tf"] = clean_hcl_output(data.get("network_hcl"))
        if data.get("security_hcl"): all_files["security.tf"] = clean_hcl_output(data.get("security_hcl"))
        if data.get("compute_hcl"): all_files["compute.tf"] = clean_hcl_output(data.get("compute_hcl"))
        if data.get("data_hcl"): all_files["data.tf"] = clean_hcl_output(data.get("data_hcl"))
    elif isinstance(data, str) and phase_filename:
        if phase_filename in all_files:
            all_files[phase_filename] = clean_hcl_output(data)

    # 3. Consolidate variables using the post-processing filter
    cleaned_files, variables_tf_content = consolidate_terraform_variables(all_files)
    
    # Update variables.tf content in our dictionary
    cleaned_files["variables.tf"] = variables_tf_content

    # 4. Write all cleaned files to disk
    for filename, content in cleaned_files.items():
        filepath = os.path.join(workspace_dir, filename)
        clean_content = content.strip() if content else ""
        
        # Filter out the non-HCL conversational text generated by the LLM (like "No resources required.")
        if not clean_content or "resources required." in clean_content.lower():
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write("")
            except Exception as e:
                print(f"Error writing empty file {filename}: {e}")
        else:
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(clean_content + "\n")
            except Exception as e:
                print(f"Error writing file {filename}: {e}")

    # Deduplicate resources across the newly written files
    scrub_workspace_variables(workspace_dir)
    post_process_hcl_compliance(workspace_dir)
    deduplicate_resources(workspace_dir)
    return True


def fix_malformed_security_groups(hcl_content: str) -> str:
    """
    Scans generated HCL and ensures that any ingress or egress block 
    containing empty parameters is either populated with default fallback 
    values or cleaned to avoid compiler crashes.
    """
    # Pattern to catch empty or broken blocks missing required parameters
    # This acts as an immediate firewall for incomplete structural components
    if "ingress {" in hcl_content or "egress {" in hcl_content:
        # Check if an ingress block is missing explicit port definitions
        lines = hcl_content.splitlines()
        modified_lines = []
        inside_sg = False
        inside_rule = False
        rule_has_ports = False
        rule_type = None

        for line in lines:
            if "resource \"aws_security_group\"" in line:
                inside_sg = True
            
            if inside_sg and ("ingress {" in line or "egress {" in line):
                inside_rule = True
                rule_type = "ingress" if "ingress" in line else "egress"
                rule_has_ports = False
                modified_lines.append(line)
                continue
                
            if inside_rule:
                if "from_port" in line or "to_port" in line:
                    rule_has_ports = True
                if line.strip() == "}":
                    inside_rule = False
                    if not rule_has_ports:
                        # Inject fallback parameters for wide-open definitions
                        modified_lines.append('    from_port   = 0')
                        modified_lines.append('    to_port     = 0')
                        modified_lines.append('    protocol    = "-1"')
                        modified_lines.append('    cidr_blocks = ["0.0.0.0/0"]')
                    modified_lines.append(line)
                    continue
                    
            modified_lines.append(line)
        return "\n".join(modified_lines)
        
    return hcl_content


def post_process_hcl_compliance(workspace_dir: str) -> None:
    """
    Applies automated HCL syntax corrections for common LLM hallucinations
    (e.g., tags inside aws_autoscaling_group, status inside aws_s3_bucket).
    """
    import glob
    import re
    
    tf_files = glob.glob(os.path.join(workspace_dir, "*.tf"))
    for tf_file in tf_files:
        if not os.path.exists(tf_file):
            continue
        try:
            with open(tf_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue
            
        modified = False
        new_content = content
        
        # Nuclear Option: Aggressively remove unrequested/hallucinated internet gateways and route tables
        current_dir = os.path.dirname(os.path.abspath(__file__))
        mock_infra_path = os.path.abspath(os.path.join(current_dir, "..", "scanner", "mock_infra.json"))
        has_igw = False
        has_rt = False
        if os.path.exists(mock_infra_path):
            try:
                import json
                with open(mock_infra_path, "r", encoding="utf-8") as f:
                    m_data = json.load(f)
                resources_list = m_data.get("resources", [])
                has_igw = any(r.get("type") == "aws_internet_gateway" for r in resources_list)
                has_rt = any(r.get("type") in ["aws_route_table", "aws_route_table_association"] for r in resources_list)
            except Exception:
                has_igw = True
                has_rt = True
        else:
            has_igw = True
            has_rt = True

        if not has_igw:
            igw_matches = list(re.finditer(r'resource\s+"aws_internet_gateway"\s+"([^"]+)"\s*\{', new_content))
            for match in reversed(igw_matches):
                start_idx = match.end() - 1
                brace_count = 0
                block_len = 0
                has_ended = False
                for char in new_content[start_idx:]:
                    block_len += 1
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            has_ended = True
                            break
                if has_ended:
                    full_block_end = start_idx + block_len
                    import_pattern = r'import\s*\{\s*to\s*=\s*aws_internet_gateway\.' + re.escape(match.group(1)) + r'\s*id\s*=\s*"[^"]+"\s*\}'
                    new_content = re.sub(import_pattern, '', new_content)
                    new_content = new_content[:match.start()] + new_content[full_block_end:]
                    modified = True
                    print(f"[Corrector] Removed hallucinated aws_internet_gateway block: {match.group(1)}")

        if not has_rt:
            rt_pattern = r'resource\s+"(aws_route_table|aws_route_table_association)"\s+"([^"]+)"\s*\{'
            rt_matches = list(re.finditer(rt_pattern, new_content))
            for match in reversed(rt_matches):
                res_type = match.group(1)
                res_name = match.group(2)
                start_idx = match.end() - 1
                brace_count = 0
                block_len = 0
                has_ended = False
                for char in new_content[start_idx:]:
                    block_len += 1
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            has_ended = True
                            break
                if has_ended:
                    full_block_end = start_idx + block_len
                    import_pattern = r'import\s*\{\s*to\s*=\s*' + re.escape(res_type) + r'\.' + re.escape(res_name) + r'\s*id\s*=\s*"[^"]+"\s*\}'
                    new_content = re.sub(import_pattern, '', new_content)
                    new_content = new_content[:match.start()] + new_content[full_block_end:]
                    modified = True
                    print(f"[Corrector] Removed hallucinated {res_type} block: {res_name}")

        # Pre-cleanup: Fix "to = resource.aws_..." and replace hyphens in resource names/labels
        original_content = new_content
        # 1. Fix "to = resource.aws_..." in import blocks
        new_content = re.sub(r'\bto\s*=\s*resource\.aws_', 'to = aws_', new_content)
        
        # 2. Find all resource names and replace hyphens with underscores
        resource_pattern = r'\b(resource|data)\s+"([a-zA-Z0-9_]+)"\s+"([a-zA-Z0-9_-]+)"\s*\{'
        resource_matches = re.findall(resource_pattern, new_content)
        
        import_pattern = r'\bto\s*=\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_-]+)\b'
        import_matches = re.findall(import_pattern, new_content)
        
        names_with_hyphens = set()
        for _, _, res_name in resource_matches:
            if "-" in res_name:
                names_with_hyphens.add(res_name)
        for _, res_name in import_matches:
            if "-" in res_name:
                names_with_hyphens.add(res_name)
                
        for name in names_with_hyphens:
            clean_name = name.replace("-", "_")
            new_content = new_content.replace(f'"{name}"', f'"{clean_name}"')
            new_content = re.sub(r'\b' + re.escape(name) + r'\b', clean_name, new_content)
            
        if new_content != original_content:
            modified = True

        # 1. Fix aws_autoscaling_group "tags = ..." or "tags_all = ..."
        asg_matches = list(re.finditer(r'resource\s+"aws_autoscaling_group"\s+"([^"]+)"\s*\{', new_content))
        for match in reversed(asg_matches):
            start_idx = match.end() - 1
            brace_count = 0
            block_content = []
            has_ended = False
            for char in new_content[start_idx:]:
                block_content.append(char)
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        has_ended = True
                        break
                        
            if has_ended:
                block_str = "".join(block_content)
                full_block_start = match.start()
                full_block_end = start_idx + len(block_str)
                
                asg_tags_match = re.search(r'\btags?\s*=\s*([\[\{])', block_str)
                if asg_tags_match:
                    tags_start = asg_tags_match.end() - 1
                    opener = asg_tags_match.group(1)
                    closer = ']' if opener == '[' else '}'
                    
                    t_brace_count = 0
                    tags_block = []
                    tags_ended = False
                    for char in block_str[tags_start:]:
                        tags_block.append(char)
                        if char == opener:
                            t_brace_count += 1
                        elif char == closer:
                            t_brace_count -= 1
                            if t_brace_count == 0:
                                tags_ended = True
                                break
                                
                    if tags_ended:
                        full_tags_decl = block_str[asg_tags_match.start():tags_start + len(tags_block)]
                        tags_text = "".join(tags_block)
                        inline_tags_str = ""
                        
                        seen_keys = set()
                        pairs = re.findall(r'["\']?([a-zA-Z0-9_.-]+)["\']?\s*[=:]\s*["\']?([^"\'\n]+)["\']?', tags_text)
                        for k, v in pairs:
                            k_clean = k.strip()
                            v_clean = v.strip()
                            if k_clean in ["key", "value", "propagate_at_launch"]:
                                continue
                            if k_clean not in seen_keys:
                                seen_keys.add(k_clean)
                                inline_tags_str += f'\n  tag {{\n    key                 = "{k_clean}"\n    value               = "{v_clean}"\n    propagate_at_launch = true\n  }}'
                        
                        list_maps = re.findall(r'\{\s*key\s*=\s*["\']?([^"\']+)["\']?\s*value\s*=\s*["\']?([^"\']+)["\']?(?:\s*propagate_at_launch\s*=\s*(true|false))?\s*\}', tags_text)
                        for l_key, l_val, l_prop in list_maps:
                            k_clean = l_key.strip()
                            v_clean = l_val.strip()
                            prop = l_prop.strip() if l_prop else "true"
                            if k_clean not in seen_keys:
                                seen_keys.add(k_clean)
                                inline_tags_str += f'\n  tag {{\n    key                 = "{k_clean}"\n    value               = "{v_clean}"\n    propagate_at_launch = {prop}\n  }}'

                        new_block_str = block_str.replace(full_tags_decl, inline_tags_str)
                        new_content = new_content[:full_block_start] + f'resource "aws_autoscaling_group" "{match.group(1)}" ' + new_block_str + new_content[full_block_end:]
                        modified = True
                        print(f"[Corrector] Converted tags in aws_autoscaling_group.{match.group(1)} to inline tag blocks.")

        # 2. Fix aws_s3_bucket versioning: split into aws_s3_bucket_versioning
        s3_bucket_matches = list(re.finditer(r'resource\s+"aws_s3_bucket"\s+"([^"]+)"\s*\{', new_content))
        for match in reversed(s3_bucket_matches):
            bucket_local_name = match.group(1)
            start_idx = match.end() - 1
            brace_count = 0
            block_content = []
            has_ended = False
            for char in new_content[start_idx:]:
                block_content.append(char)
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        has_ended = True
                        break
                        
            if has_ended:
                block_str = "".join(block_content)
                full_block_start = match.start()
                full_block_end = start_idx + len(block_str)
                
                versioning_match = re.search(r'\bversioning\s*\{', block_str)
                if versioning_match:
                    v_start = versioning_match.end() - 1
                    v_brace_count = 0
                    v_block = []
                    v_ended = False
                    for char in block_str[v_start:]:
                        v_block.append(char)
                        if char == '{':
                            v_brace_count += 1
                        elif char == '}':
                            v_brace_count -= 1
                            if v_brace_count == 0:
                                v_ended = True
                                break
                                
                    if v_ended:
                        full_versioning_decl = block_str[versioning_match.start():v_start + len(v_block)]
                        versioning_body = "".join(v_block[1:-1])  # Exclude braces
                        
                        # Determine status
                        status = "Enabled"
                        if "disabled" in versioning_body.lower() or "false" in versioning_body.lower():
                            status = "Suspended"
                        
                        # Remove versioning block from aws_s3_bucket
                        new_block_str = block_str.replace(full_versioning_decl, "")
                        
                        # Build the new aws_s3_bucket_versioning resource block
                        safe_suffix = bucket_local_name.replace("-", "_")
                        versioning_resource = f'\n\nresource "aws_s3_bucket_versioning" "{safe_suffix}_versioning" {{\n  bucket = aws_s3_bucket.{bucket_local_name}.id\n  versioning_configuration {{\n    status = "{status}"\n  }}\n}}'
                        
                        new_content = new_content[:full_block_start] + f'resource "aws_s3_bucket" "{bucket_local_name}" ' + new_block_str + versioning_resource + new_content[full_block_end:]
                        modified = True
                        print(f"[Corrector] Extracted versioning from aws_s3_bucket.{bucket_local_name} into aws_s3_bucket_versioning.")

        # 3. Fix aws_db_instance subnet_ids
        db_matches = list(re.finditer(r'resource\s+"aws_db_instance"\s+"([^"]+)"\s*\{', new_content))
        for match in reversed(db_matches):
            db_local_name = match.group(1)
            start_idx = match.end() - 1
            brace_count = 0
            block_content = []
            has_ended = False
            for char in new_content[start_idx:]:
                block_content.append(char)
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        has_ended = True
                        break
                        
            if has_ended:
                block_str = "".join(block_content)
                full_block_start = match.start()
                full_block_end = start_idx + len(block_str)
                
                subnet_ids_match = re.search(r'\bsubnet_ids\s*=\s*\[([\s\S]*?)\]', block_str)
                if subnet_ids_match:
                    subnet_ids_str = subnet_ids_match.group(0)
                    subnet_ids_list_str = subnet_ids_match.group(1).strip()
                    
                    # Remove subnet_ids line from aws_db_instance
                    new_block_str = block_str.replace(subnet_ids_str, "")
                    
                    # Ensure we set db_subnet_group_name
                    db_sng_ref = f"aws_db_subnet_group.{db_local_name}_subnet_group.name"
                    if "db_subnet_group_name" not in new_block_str:
                        # Append db_subnet_group_name inside aws_db_instance before the last closing brace
                        if new_block_str.rstrip().endswith("}"):
                            pos = new_block_str.rfind("}")
                            new_block_str = new_block_str[:pos] + f'\n  db_subnet_group_name = {db_sng_ref}\n' + new_block_str[pos:]
                    
                    # Generate separate aws_db_subnet_group block
                    subnet_group_resource = f'\n\nresource "aws_db_subnet_group" "{db_local_name}_subnet_group" {{\n  name        = "{db_local_name.replace("_", "-")}-subnet-group"\n  subnet_ids  = [{subnet_ids_list_str}]\n  description = "Database subnet group for {db_local_name}"\n}}'
                    
                    new_content = new_content[:full_block_start] + f'resource "aws_db_instance" "{db_local_name}" ' + new_block_str + subnet_group_resource + new_content[full_block_end:]
                    modified = True
                    print(f"[Corrector] Split subnet_ids from aws_db_instance.{db_local_name} into separate aws_db_subnet_group resource.")

        # 4. Fix malformed security groups
        cleaned_sg_content = fix_malformed_security_groups(new_content)
        if cleaned_sg_content != new_content:
            new_content = cleaned_sg_content
            modified = True

        if modified:
            try:
                with open(tf_file, "w", encoding="utf-8") as f:
                    f.write(new_content)
            except Exception as e:
                print(f"Error writing corrected file {tf_file}: {e}")


def execute_terraform_validation(workspace_dir: str = "terraform_workspace") -> dict:
    """
    Runs offline AST syntax formatting, then local semantic validation.
    """
    # 1. Format the HCL (AST Syntax Check)
    try:
        fmt_result = subprocess.run(
            ["terraform", "fmt"],
            cwd=workspace_dir,
            capture_output=True,
        )
    except FileNotFoundError:
        return {"is_valid": False, "error_logs": ["Terraform binary not found. Ensure Terraform is installed and in your PATH."]}

    if fmt_result.returncode != 0:
        stderr = fmt_result.stderr.decode() if isinstance(fmt_result.stderr, (bytes, bytearray)) else (fmt_result.stderr or "")
        return {"is_valid": False, "error_logs": ["Syntax Error: " + stderr.strip()]}

    # 2. Define the path to your custom .terraformrc
    project_root = os.path.abspath(os.path.join(workspace_dir, ".."))
    env = os.environ.copy()
    env["TF_CLI_CONFIG_FILE"] = f"{project_root}{os.sep}.terraformrc"

    # 3. Offline Initialization (Using the local filesystem mirror)
    try:
        init_result = subprocess.run(
            ["terraform", "init", "-backend=false"],
            cwd=workspace_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return {"is_valid": False, "error_logs": ["Init timed out."]}

    if init_result.returncode != 0:
        return {"is_valid": False, "error_logs": [f"Init Failed: {init_result.stderr}"]}

    # 4. Deep Semantic Validation
    try:
        val_result = subprocess.run(
            ["terraform", "validate", "-json"],
            cwd=workspace_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return {"is_valid": False, "error_logs": ["Validation timed out."]}

    if val_result.returncode == 0:
        return {"is_valid": True, "error_logs": []}

    # 5. Parse JSON semantic errors to feed back to the LLM
    try:
        val_data = json.loads(val_result.stdout)
        errors = []
        for diag in val_data.get('diagnostics', []):
            if diag.get('severity') == 'error':
                error_msg = f"{diag.get('summary')}: {diag.get('detail', '')}"
                errors.append(error_msg.strip())

        return {"is_valid": False, "error_logs": errors}

    except json.JSONDecodeError:
        return {"is_valid": False, "error_logs": [val_result.stderr]}


def parse_dependencies(workspace_dir: str) -> dict:
    """
    Parses variables, local mappings, resources, and data blocks from 
    all HCL (.tf) files in the workspace directory.
    Constructs an internal map of resource dependencies.
    """
    import re
    import glob
    
    defined_resources = set()
    resource_blocks = {}
    
    # Scan and find all resources and local/variable definitions
    tf_files = glob.glob(os.path.join(workspace_dir, "*.tf"))
    
    for tf_file in tf_files:
        if not os.path.exists(tf_file):
            continue
        try:
            with open(tf_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"Failed to read {tf_file} for dependency parsing: {e}")
            continue
            
        # Extract variables
        variables = re.findall(r'variable\s+"([^"]+)"\s*\{', content)
        for var in variables:
            full_name = f"var.{var}"
            defined_resources.add(full_name)
            resource_blocks[full_name] = ""
            
        # Extract resources
        for match in re.finditer(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', content):
            res_type = match.group(1)
            res_name = match.group(2)
            full_name = f"{res_type}.{res_name}"
            defined_resources.add(full_name)
            
            # Extract block content by matching braces
            start_idx = match.end() - 1
            brace_count = 0
            block_content = []
            for char in content[start_idx:]:
                block_content.append(char)
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        break
            resource_blocks[full_name] = "".join(block_content)
            
        # Extract data sources
        for match in re.finditer(r'data\s+"([^"]+)"\s+"([^"]+)"\s*\{', content):
            res_type = match.group(1)
            res_name = match.group(2)
            full_name = f"data.{res_type}.{res_name}"
            defined_resources.add(full_name)
            
            start_idx = match.end() - 1
            brace_count = 0
            block_content = []
            for char in content[start_idx:]:
                block_content.append(char)
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        break
            resource_blocks[full_name] = "".join(block_content)
            
        # Extract locals
        for match in re.finditer(r'locals\s*\{', content):
            start_idx = match.end() - 1
            brace_count = 0
            block_content = []
            for char in content[start_idx:]:
                block_content.append(char)
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        break
            locals_text = "".join(block_content)
            
            # Line by line parsing of local assignments
            for line in locals_text.splitlines():
                line_clean = line.strip()
                if line_clean.startswith("#") or line_clean.startswith("//"):
                    continue
                assign_match = re.match(r'^([a-zA-Z0-9_-]+)\s*=\s*(.*)', line_clean)
                if assign_match:
                    var_name = assign_match.group(1)
                    var_val = assign_match.group(2)
                    full_name = f"local.{var_name}"
                    defined_resources.add(full_name)
                    resource_blocks[full_name] = var_val

    # Map resources to their referenced dependencies
    dependency_map = {}
    for res, block_text in resource_blocks.items():
        dependencies = set()
        for other_res in defined_resources:
            if other_res == res:
                continue
            pattern = r'\b' + re.escape(other_res) + r'\b'
            if re.search(pattern, block_text):
                dependencies.add(other_res)
        dependency_map[res] = list(dependencies)
        
    return dependency_map


def find_missing_references(workspace_dir: str, dependency_map: dict) -> list:
    """
    Scans the resource blocks for any references of resources, variables, data sources,
    or local values, and flags if those targets are NOT defined in our files.
    """
    import re
    import glob

    defined_keys = set(dependency_map.keys())
    tf_files = glob.glob(os.path.join(workspace_dir, "*.tf"))
    missing_errors = []

    # Matches typical references:
    # aws_vpc.main, var.vpc_id, local.vpc_id, data.aws_vpc.main
    ref_pattern = r'\b((?:aws_[a-zA-Z0-9_-]+|var|local|data\.[a-zA-Z0-9_-]+)\.[a-zA-Z0-9_-]+)\b'

    for tf_file in tf_files:
        if not os.path.exists(tf_file):
            continue
        try:
            with open(tf_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue

        for match in re.finditer(r'(resource|data)\s+"([^"]+)"\s+"([^"]+)"\s*\{', content):
            res_type = match.group(1)
            res_type_full = match.group(2)
            res_name = match.group(3)
            current_resource = f"{res_type_full}.{res_name}" if res_type == "resource" else f"data.{res_type_full}.{res_name}"

            start_idx = match.end() - 1
            brace_count = 0
            block_content = []
            for char in content[start_idx:]:
                block_content.append(char)
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        break
            block_text = "".join(block_content)

            references = re.findall(ref_pattern, block_text)
            for ref in references:
                if ref == current_resource:
                    continue
                if ref.startswith("path.") or ref.startswith("each.") or ref.startswith("count."):
                    continue
                if ref not in defined_keys:
                    missing_errors.append(
                        f"Resource '{current_resource}' references '{ref}', but '{ref}' is not defined in the workspace."
                    )

    return missing_errors


def detect_cycles(dependency_map: dict) -> list:
    """
    Detects circular dependencies in the dependency map using DFS.
    Returns a list of cycle paths if any exist.
    """
    visited = {}  # 0 = unvisited, 1 = visiting, 2 = visited
    cycles = []
    
    def dfs(node, path):
        visited[node] = 1
        for neighbor in dependency_map.get(node, []):
            if neighbor not in dependency_map:
                continue
            if visited.get(neighbor, 0) == 1:
                # Cycle detected
                cycle_path = path + [neighbor]
                try:
                    cycle_start = cycle_path.index(neighbor)
                    cycles.append(cycle_path[cycle_start:])
                except ValueError:
                    cycles.append(cycle_path)
            elif visited.get(neighbor, 0) == 0:
                dfs(neighbor, path + [neighbor])
        visited[node] = 2

    for node in dependency_map:
        if visited.get(node, 0) == 0:
            dfs(node, [node])
            
    return cycles


def generate_terraform_graph(workspace_path: str) -> None:
    """
    Runs terraform init, generates a dependency graph DOT file, and tries
    to convert it to PNG if graphviz is available.
    """
    try:
        project_root = os.path.abspath(os.path.join(workspace_path, ".."))
        env = os.environ.copy()
        env["TF_CLI_CONFIG_FILE"] = f"{project_root}{os.sep}.terraformrc"

        subprocess.run(
            ["terraform", "init", "-backend=false"],
            cwd=workspace_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=True
        )

        dot_file_path = os.path.join(workspace_path, "dependency_graph.dot")
        with open(dot_file_path, "w", encoding="utf-8") as f:
            subprocess.run(
                ["terraform", "graph", "-type=plan"], 
                cwd=workspace_path, 
                env=env,
                stdout=f, 
                timeout=15,
                check=True
            )
        
        try:
            subprocess.run(
                ["dot", "-Tpng", "dependency_graph.dot", "-o", "dependency_graph.png"], 
                cwd=workspace_path, 
                capture_output=True,
                check=True
            )
            print("Dependency graph PNG generated successfully.")
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("Graphviz 'dot' command not found or failed. Skipping PNG generation.")
        
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"Graph generation failed: {e}")


def generate_png_graph(state: dict, workspace_dir: str = "terraform_workspace") -> str:
    """
    Generates a DOT representation of the infrastructure_graph and converts it
    to a PNG image file using Graphviz's dot utility.
    """
    infra_graph = state.get("infrastructure_graph", {"nodes": {}, "edges": []})
    nodes = infra_graph.get("nodes", {})
    edges = infra_graph.get("edges", [])

    # Group nodes by domain
    domains = {
        "Network": [],
        "Security": [],
        "Compute": [],
        "Data & Serverless": []
    }

    for node_key, node_data in nodes.items():
        res_type = node_data.get("type", "")
        if res_type in ["aws_vpc", "aws_subnet", "aws_internet_gateway", "aws_nat_gateway"]:
            domains["Network"].append((node_key, node_data))
        elif res_type in ["aws_security_group", "aws_iam_role", "aws_iam_policy"]:
            domains["Security"].append((node_key, node_data))
        elif res_type in ["aws_instance", "aws_eks_cluster", "aws_eks_node_group", "aws_autoscaling_group", "aws_launch_template"]:
            domains["Compute"].append((node_key, node_data))
        else:
            domains["Data & Serverless"].append((node_key, node_data))

    dot_lines = [
        "digraph G {",
        "  rankdir=TB;",
        '  node [style="filled", shape="box", fontname="Arial"];'
    ]

    domain_styles = {
        "Network": 'style=filled; color="#82b366"; fillcolor="#f5fbf0"; label="Network";',
        "Security": 'style=filled; color="#b85450"; fillcolor="#fdf6f6"; label="Security";',
        "Compute": 'style=filled; color="#6c8ebf"; fillcolor="#f0f4f9"; label="Compute";',
        "Data & Serverless": 'style=filled; color="#d79b00"; fillcolor="#fffaf0"; label="Data & Serverless";'
    }

    cluster_idx = 0
    for domain_name, res_list in domains.items():
        if not res_list:
            continue
            
        dot_lines.append(f'  subgraph cluster_{cluster_idx} {{')
        dot_lines.append(f'    {domain_styles[domain_name]}')
        
        for node_key, node_data in res_list:
            name = node_data.get("name") or node_key
            if domain_name == "Network":
                color = "#ffe6cc"
            elif domain_name == "Security":
                color = "#f8cecc"
            elif domain_name == "Compute":
                color = "#dae8fc"
            else:
                color = "#fff2cc"
                
            dot_lines.append(f'    "{node_key}" [label="{name}\\n({node_data.get("type", "")})", fillcolor="{color}"];')
            
        dot_lines.append("  }")
        cluster_idx += 1

    # Add edges
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        relation = edge.get("relation", "")
        dot_lines.append(f'  "{source}" -> "{target}" [label="{relation}"];')

    dot_lines.append("}")

    dot_content = "\n".join(dot_lines)
    dot_file_path = os.path.join(workspace_dir, "architecture.dot")
    png_file_path = os.path.join(workspace_dir, "architecture.png")

    with open(dot_file_path, "w", encoding="utf-8") as f:
        f.write(dot_content)

    # Convert dot to png using the dot CLI tool
    try:
        subprocess.run(
            ["dot", "-Tpng", "architecture.dot", "-o", "architecture.png"],
            cwd=workspace_dir,
            capture_output=True,
            check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise RuntimeError(f"Graphviz 'dot' command execution failed or not found: {str(e)}")

    return png_file_path


def generate_drawio_xml(state: dict, workspace_dir: str = "terraform_workspace") -> str:
    """
    Generates a standard Draw.io/mxGraph XML representation of the infrastructure_graph,
    applying a clean layout with custom color tiers for resource categories.
    """
    import math

    infra_graph = state.get("infrastructure_graph", {"nodes": {}, "edges": []})
    nodes = infra_graph.get("nodes", {})
    edges = infra_graph.get("edges", [])

    xml_lines = [
        '<mxfile host="Electron" version="21.6.8" type="device">',
        '  <diagram id="diagram_1" name="Architecture Map">',
        '    <mxGraphModel dx="1000" dy="1000" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="850" pageHeight="1100" math="0" shadow="0">',
        '      <root>',
        '        <mxCell id="0" />',
        '        <mxCell id="1" parent="0" />'
    ]

    # Arrange nodes in a grid layout
    cols = math.ceil(math.sqrt(len(nodes))) if nodes else 1
    x_spacing = 220
    y_spacing = 160
    x_start = 60
    y_start = 60

    # Map node key (e.g. "aws_vpc.main") to a unique mxGraph ID
    node_ids = {}
    for idx, (node_key, node_data) in enumerate(nodes.items()):
        node_ids[node_key] = f"node_{idx + 2}"
        
        # Calculate grid position
        row = idx // cols
        col = idx % cols
        x = x_start + col * x_spacing
        y = y_start + row * y_spacing
        
        name = node_data.get("name") or node_key
        # Custom color styling according to standard IaC tiers
        style = "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;"
        if "vpc" in node_key:
            style = "rounded=1;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;"
        elif "subnet" in node_key:
            style = "rounded=1;whiteSpace=wrap;html=1;fillColor=#ffe6cc;strokeColor=#d79b00;"
        elif "security" in node_key:
            style = "rounded=1;whiteSpace=wrap;html=1;fillColor=#f8cecc;strokeColor=#b85450;"
            
        xml_lines.append(
            f'        <mxCell id="{node_ids[node_key]}" value="{name}" style="{style}" vertex="1" parent="1">'
            f'          <mxGeometry x="{x}" y="{y}" width="140" height="60" as="geometry" />'
            f'        </mxCell>'
        )

    for idx, edge in enumerate(edges):
        source = edge.get("source")
        target = edge.get("target")
        relation = edge.get("relation", "")
        
        source_cell = node_ids.get(source)
        target_cell = node_ids.get(target)
        
        if source_cell and target_cell:
            edge_id = f"edge_{idx}"
            xml_lines.append(
                f'        <mxCell id="{edge_id}" value="{relation}" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#B3B3B3;" edge="1" parent="1" source="{source_cell}" target="{target_cell}">'
                f'          <mxGeometry relative="1" as="geometry" />'
                f'        </mxCell>'
            )

    xml_lines.extend([
        '      </root>',
        '    </mxGraphModel>',
        '  </diagram>',
        '</mxfile>'
    ])

    drawio_path = os.path.join(workspace_dir, "architecture.drawio")
    with open(drawio_path, "w", encoding="utf-8") as f:
        f.write("\n".join(xml_lines))
    return drawio_path


def scrub_deprecated_s3_syntax(workspace_dir: str = "terraform_workspace"):
    """
    A context-aware parser that forcefully removes deprecated S3 arguments
    ONLY when they occur inside an 'aws_s3_bucket' resource block.
    """
    import os
    import re

    if not os.path.exists(workspace_dir):
        return

    for filename in os.listdir(workspace_dir):
        if not filename.endswith(".tf"):
            continue
            
        filepath = os.path.join(workspace_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        new_lines = []
        inside_s3_bucket = False
        bucket_brace_depth = 0
        skip_brackets = 0
        
        for line in lines:
            # Detect if we are entering an aws_s3_bucket block
            if re.match(r'^\s*resource\s+"aws_s3_bucket"\s+', line):
                inside_s3_bucket = True
                bucket_brace_depth = line.count('{') - line.count('}')
                new_lines.append(line)
                continue

            if inside_s3_bucket:
                # If we are currently ignoring a multi-line forbidden block, track the brackets
                if skip_brackets > 0:
                    skip_brackets += line.count('{')
                    skip_brackets -= line.count('}')
                    continue # Skip writing this line
                    
                # Detect forbidden S3 arguments (matches 'versioning =', 'versioning {', 'acl =', etc.)
                if re.match(r'^\s*(versioning|server_side_encryption|server_side_encryption_configuration|acl)\s*(=|\{)', line) or re.match(r'^\s*acl\s*=', line):
                    # Count brackets on this specific line to handle inline/multi-line blocks
                    skip_brackets += line.count('{')
                    skip_brackets -= line.count('}')
                    continue # Skip writing this line
                
                # If we are not skipping, update the bucket brace depth
                bucket_brace_depth += line.count('{')
                bucket_brace_depth -= line.count('}')
                
                if bucket_brace_depth <= 0:
                    inside_s3_bucket = False

            # If it passes the firewall, keep the line
            new_lines.append(line)
            
        # Overwrite the file with the sanitized code
        sanitized_content = "".join(new_lines)
        if "key_schema" in sanitized_content:
            sanitized_content = re.sub(r'key_schema\s*\{\s*(.*?)\s*\}', r'\1', sanitized_content, flags=re.DOTALL)
            
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(sanitized_content)


def filter_aws_input_data(aws_input_data: dict, node_type: str) -> dict:
    """Filters the telemetry payload so nodes only receive their respective domain resources."""
    if not aws_input_data or not isinstance(aws_input_data, dict):
        return {}
        
    filtered = {
        "region": aws_input_data.get("region", "us-east-1"),
        "vpc_id": aws_input_data.get("vpc_id"),
        "resources": []
    }
    
    if node_type == "network":
        allowed = ["aws_vpc", "aws_subnet", "aws_internet_gateway", "aws_nat_gateway"]
    elif node_type == "security":
        allowed = ["aws_iam_role", "aws_iam_policy", "aws_security_group"]
    elif node_type == "compute":
        allowed = ["aws_instance", "aws_eks_cluster", "aws_eks_node_group", "aws_autoscaling_group", "aws_launch_template"]
    elif node_type == "data":
        allowed = [
            "aws_s3_bucket", "aws_db_instance", "aws_dynamodb_table",
            "aws_sqs_queue", "aws_lambda_function", "aws_lambda_event_source_mapping"
        ]
    else:
        allowed = []
        
    filtered["resources"] = [
        r for r in aws_input_data.get("resources", [])
        if r.get("type") in allowed
    ]
    return filtered

