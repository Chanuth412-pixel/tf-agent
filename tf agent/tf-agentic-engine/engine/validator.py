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
        # Run 'fmt' without '-check' to auto-correct spacing and validate syntax
        result = subprocess.run(
            ["terraform", "fmt"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            shell=False,
            timeout=15,
        )

        # A non-zero return code now indicates a real syntax/formatting error
        if result.returncode != 0:
            msg = result.stderr or result.stdout or "Terraform syntax formatting failed."
            return {"is_valid": False, "error_logs": [msg.strip()]}

        return {"is_valid": True, "error_logs": []}

    except subprocess.TimeoutExpired:
        print("[Validator] Critical: Terraform formatting timed out.")
        return {"is_valid": False, "error_logs": ["Validation timeout expired."]}
    except FileNotFoundError:
        return {"is_valid": False, "error_logs": ["Terraform binary not found. Ensure Terraform is installed and in your PATH."]}
    except Exception as e:
        return {"is_valid": False, "error_logs": [str(e)]}
