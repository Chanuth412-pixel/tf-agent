import os
import subprocess
from typing import Tuple


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


def execute_terraform_validation() -> Tuple[bool, str]:
    """
    Executes 'terraform validate' within the terraform_workspace directory.
    Returns a tuple of (is_valid, error_message).
    """
    workspace_dir = "terraform_workspace"

    # Ensure workspace exists
    if not os.path.isdir(workspace_dir):
        return False, f"Workspace directory '{workspace_dir}' does not exist."

    try:
        # Run `terraform init -input=false` to ensure providers are initialized (no interactive prompts)
        init = subprocess.run(
            ["terraform", "init", "-input=false"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            shell=False,
        )
        if init.returncode != 0:
            msg = init.stderr or init.stdout
            return False, f"terraform init failed: {msg.strip()}"

        # Run the validate command and request JSON output where supported
        result = subprocess.run(
            ["terraform", "validate", "-json"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            shell=False,
        )

        if result.returncode == 0:
            return True, ""
        else:
            error_msg = result.stderr if result.stderr else result.stdout
            return False, (error_msg or "Unknown validation error").strip()

    except FileNotFoundError:
        return False, "Terraform binary not found. Ensure Terraform is installed and in your PATH."
    except Exception as e:
        return False, str(e)
