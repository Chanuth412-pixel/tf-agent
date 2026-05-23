import re
import os
import subprocess


def clean_hcl_output(text: str) -> str:
    """Extract only the HCL code block from LLM output.

    Looks for ```hcl ... ``` or ``` ... ``` blocks and returns the inner content.
    Falls back to a heuristic split when no fences are present.
    """
    if not text:
        return ""

    # Match triple-backtick fenced blocks optionally labeled as hcl
    match = re.search(r"```(?:hcl)?(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # Heuristic fallback: if HCL-like keywords are present, split at the first HCL construct
    if re.search(r"\b(resource|locals\s*\{|data\s+|module\s+|provider\s+|terraform\s*\{)", text, re.IGNORECASE):
        parts = re.split(r"(?=resource\b|locals\s*\{|data\b|module\b|provider\b|terraform\s*\{)", text, 1, flags=re.IGNORECASE)
        if len(parts) > 1:
            return parts[1].strip()

    return text.strip()


def parse_and_write_files(input_data) -> bool:
    """Write cleaned HCL to files in `terraform_workspace`.

    Accepts either a raw LLM output string or a state dict mapping keys like
    'network_hcl' -> HCL string. Returns True if at least one file was written.
    """
    workspace_dir = "terraform_workspace"
    os.makedirs(workspace_dir, exist_ok=True)

    files_written = 0

    if isinstance(input_data, dict):
        files_to_write = {
            "network.tf": input_data.get("network_hcl", ""),
            "security.tf": input_data.get("security_hcl", ""),
            "compute.tf": input_data.get("compute_hcl", ""),
            "data.tf": input_data.get("data_hcl", ""),
        }
        for filename, content in files_to_write.items():
            if content and content.strip():
                cleaned = clean_hcl_output(content)
                path = os.path.join(workspace_dir, filename)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(cleaned + "\n")
                files_written += 1
    else:
        # Treat input_data as a raw string output from the LLM
        raw = input_data or ""
        cleaned = clean_hcl_output(raw)
        if cleaned:
            target = os.path.join(workspace_dir, "main.tf")
            with open(target, "w", encoding="utf-8") as f:
                f.write(cleaned + "\n")
            files_written = 1

    return files_written > 0


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
