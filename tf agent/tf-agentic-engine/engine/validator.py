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
        # Prefer offline init with pre-cached providers when available to avoid network/plugin downloads
        plugin_dir = "/usr/share/terraform/plugins"
        if os.path.isdir(plugin_dir):
            init_cmd = ["terraform", "init", f"-plugin-dir={plugin_dir}", "-get=false"]
        else:
            # Fallback for systems without a cached plugin directory
            init_cmd = ["terraform", "init", "-input=false"]

        try:
            init = subprocess.run(
                init_cmd,
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                shell=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            print("[Validator] Critical: Terraform init timed out waiting for local resources.")
            return {"is_valid": False, "error_logs": ["Terraform init timed out after 30 seconds."]}
        if init.returncode != 0:
            msg = init.stderr or init.stdout
            return {"is_valid": False, "error_logs": [f"terraform init failed: {msg.strip()}"]}

        # Run the validate command and request JSON output where supported
        result = subprocess.run(
            ["terraform", "validate", "-json"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            shell=False,
        )

        if result.returncode == 0:
            return {"is_valid": True}
        else:
            error_msg = result.stderr if result.stderr else result.stdout
            return {"is_valid": False, "error_logs": [(error_msg or "Unknown validation error").strip()]}

    except FileNotFoundError:
        return {"is_valid": False, "error_logs": ["Terraform binary not found. Ensure Terraform is installed and in your PATH."]}
    except Exception as e:
        return {"is_valid": False, "error_logs": [str(e)]}
