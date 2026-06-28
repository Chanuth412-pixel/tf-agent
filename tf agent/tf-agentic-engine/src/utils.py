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
  2. NEVER use a `tags` block. You MUST define tags using individual `tag {{ key = "Environment" value = "production" propagate_at_launch = true }}` blocks.
- For `aws_iam_role`: You MUST always specify the required **`assume_role_policy`** argument. If the exact policy document is not provided in the AWS telemetry, default to a standard EC2 service assume-role policy trust document via `jsonencode`.
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
        model=MODEL_NAME,
        temperature=0.0,
        base_url=OLLAMA_BASE_URL,
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
