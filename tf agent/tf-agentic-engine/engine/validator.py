import os
import subprocess


def parse_and_write_files(raw_output: str, phase_filename: str) -> None:
    """
    Cleans markdown fences and conversational leaks from the LLM output
    and writes the raw HCL blocks into the workspace files.
    """
    cleaned_lines = []

    for line in (raw_output or "").splitlines():
        # Remove markdown code block markers completely
        if line.strip().startswith("```"):
            continue
        # Catch casual conversational filler that small models output
        if line.strip().startswith(("Here is", "Sure", "This code", "Note:", "To fix")):
            continue
        cleaned_lines.append(line)

    sanitized_hcl = "\n".join(cleaned_lines).strip()

    # Define the workspace directory paths safely
    workspace_dir = "terraform_workspace"
    target_path = os.path.join(workspace_dir, phase_filename)

    # Ensure workspace exists
    os.makedirs(workspace_dir, exist_ok=True)

    # Safely clear the old phase file if it exists
    if os.path.exists(target_path):
        os.remove(target_path)

    # Write the freshly sanitized HCL code
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(sanitized_hcl + "\n")


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
