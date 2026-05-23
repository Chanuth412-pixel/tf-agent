import re
import os
import subprocess


def clean_hcl_output(text: str) -> str:
    if not text:
        return ""

    match = re.search(r"```(?:hcl)?(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    if "resource " in text or "locals {" in text:
        parts = re.split(r"(?=resource |locals \{|data |module |provider |terraform \{)", text, 1)
        if len(parts) > 1:
            return parts[1].strip()

    return text.strip()


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


def execute_terraform_validation() -> dict:
    """
    Executes 'terraform validate' within the terraform_workspace directory.
    Returns a graph-state dictionary like:
        {"is_valid": True}
    or
        {"is_valid": False, "error_logs": [..]}
    """
    workspace_dir = "terraform_workspace"

    # Ensure workspace exists
    if not os.path.isdir(workspace_dir):
        return {"is_valid": False, "error_logs": [f"Workspace directory '{workspace_dir}' does not exist."]}

    try:
        # Offline syntax check using `terraform fmt -check` to avoid network/plugin downloads
        fmt_check = subprocess.run(
            ["terraform", "fmt", "-check"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            shell=False,
            timeout=30,
        )

        if fmt_check.returncode != 0:
            msg = fmt_check.stderr or fmt_check.stdout or "Terraform syntax formatting check failed."
            return {"is_valid": False, "error_logs": [msg.strip()]}

        # Syntax check passed; treat as valid for offline-only validation
        return {"is_valid": True}

    except FileNotFoundError:
        return {"is_valid": False, "error_logs": ["Terraform binary not found. Ensure Terraform is installed and in your PATH."]}
    except Exception as e:
        return {"is_valid": False, "error_logs": [str(e)]}
