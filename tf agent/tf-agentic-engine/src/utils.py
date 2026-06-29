import re
import os
import subprocess
import json
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from config.settings import OLLAMA_BASE_URL, MODEL_NAME, NUM_CTX


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
- For `aws_s3_bucket`: The argument to set the bucket name is `bucket` (e.g., `bucket = "my-bucket"`). You are strictly FORBIDDEN from using `name = "my-bucket"`.
- For resource local names (the block identifier after the resource type, e.g. `resource "aws_s3_bucket" "local_name"`): You MUST replace any hyphens (`-`) in the AWS resource name with underscores (`_`) (e.g. use `tf_engine_state_table` instead of `tf-engine-state-table` for the block identifier). However, the actual resource argument fields (like `name = "..."`, `bucket = "..."`, and `id = "..."` inside the `import` block) MUST keep their original hyphens to match AWS exactly.
- Do NOT add a `description` argument to resources unless it is explicitly supported by that resource type (e.g. `aws_security_group` supports it, but `aws_autoscaling_group` and `aws_subnet` do NOT).
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
    prompt = ChatPromptTemplate.from_template(full_prompt)
    chain = prompt | llm
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


def deduplicate_variables(workspace_dir: str) -> None:
    """
    Scans all .tf files in the workspace (except variables.tf and provider.tf),
    extracts variable definitions, removes them from the source files,
    and writes them uniquely to a single variables.tf file to prevent duplicates.
    """
    import re
    import glob
    
    variables_map = {}
    tf_files = glob.glob(os.path.join(workspace_dir, "*.tf"))
    
    # 1. Read existing variables.tf if it exists to seed our variables map
    variables_tf_path = os.path.join(workspace_dir, "variables.tf")
    if os.path.exists(variables_tf_path):
        try:
            with open(variables_tf_path, "r", encoding="utf-8") as f:
                var_content = f.read()
            for match in re.finditer(r'variable\s+"([^"]+)"\s*\{', var_content):
                var_name = match.group(1)
                start_idx = match.end() - 1
                brace_count = 0
                block_content = []
                for char in var_content[start_idx:]:
                    block_content.append(char)
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            break
                variables_map[var_name] = f'variable "{var_name}" ' + "".join(block_content)
        except Exception as e:
            print(f"Error reading variables.tf: {e}")

    # 2. Scan other tf files to extract variable definitions
    for tf_file in tf_files:
        basename = os.path.basename(tf_file)
        if basename in ["variables.tf", "provider.tf"]:
            continue
            
        if not os.path.exists(tf_file):
            continue
            
        try:
            with open(tf_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue
            
        modified = False
        new_content = content
        
        matches = list(re.finditer(r'variable\s+"([^"]+)"\s*\{', new_content))
        for match in reversed(matches):
            var_name = match.group(1)
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
                variables_map[var_name] = f'variable "{var_name}" ' + block_str
                end_idx = start_idx + len(block_str)
                block_start = match.start()
                new_content = new_content[:block_start] + new_content[end_idx:]
                modified = True
                
        if modified:
            try:
                with open(tf_file, "w", encoding="utf-8") as f:
                    f.write(new_content.strip() + "\n")
            except Exception as e:
                print(f"Error writing updated {tf_file}: {e}")

    # 3. Write all collected unique variables to variables.tf
    if variables_map:
        try:
            sorted_vars = sorted(variables_map.items())
            variables_tf_content = "\n\n".join(val for name, val in sorted_vars)
            with open(variables_tf_path, "w", encoding="utf-8") as f:
                f.write(variables_tf_content.strip() + "\n")
        except Exception as e:
            print(f"Error writing variables.tf: {e}")


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

    files_to_write = {}

    # Handle full state dictionary (if called by the validator or main graph)
    if isinstance(data, dict):
        files_to_write = {
            "network.tf": data.get("network_hcl", ""),
            "security.tf": data.get("security_hcl", ""),
            "compute.tf": data.get("compute_hcl", ""),
            "data.tf": data.get("data_hcl", "")
        }
    # Handle single file generation (called directly by individual nodes)
    elif isinstance(data, str) and phase_filename:
        files_to_write = {phase_filename: data}

    for filename, content in files_to_write.items():
        if content:
            cleaned_content = clean_hcl_output(content)
            filepath = os.path.join(workspace_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(cleaned_content)

    deduplicate_variables(workspace_dir)
    deduplicate_resources(workspace_dir)
    return True


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
