import os
import re
import shutil
import subprocess


def parse_and_write_files(raw_output: str, target_dir="terraform_workspace", phase_filename: str = None):
    """Parser that writes HCL to disk.

    Behavior changes:
    - If `phase_filename` is provided, only that file is cleared before writing.
    - The function will still attempt to extract named file markers if present.
    - If no markers are present and `phase_filename` is given, raw_output is
      written directly to `phase_filename` inside `target_dir`.
    """

    print(f"[Parser] Preparing workspace at: {target_dir} (phase: {phase_filename})")

    os.makedirs(target_dir, exist_ok=True)

    files_to_write = {}
    current_filename = None
    current_content = []
    inside_code_block = False

    file_marker_pattern = re.compile(r'^[\-=/#\s]*([\w\-.]+\.tf)[\-=/#\s]*$', re.IGNORECASE)

    for line in (raw_output or "").splitlines():
        clean_line = line.strip()

        marker = file_marker_pattern.match(clean_line)
        if marker:
            if current_filename and current_content:
                files_to_write[current_filename] = "\n".join(current_content)

            current_filename = marker.group(1).lower()
            current_content = []
            inside_code_block = False
            continue

        if current_filename:
            if clean_line.startswith("```"):
                if not inside_code_block:
                    inside_code_block = True
                    continue
                else:
                    files_to_write[current_filename] = "\n".join(current_content)
                    current_filename = None
                    current_content = []
                    inside_code_block = False
                    continue

            current_content.append(line)

    if current_filename and current_content:
        files_to_write[current_filename] = "\n".join(current_content)

    # If parser didn't find any file markers, and a phase filename was provided,
    # write the raw output directly into that phase file.
    if not files_to_write and phase_filename:
        print("[Parser] No markers found; writing raw output into phase file")
        files_to_write[phase_filename] = raw_output

    # Before writing, if a specific phase file is targeted, remove only that file.
    if phase_filename:
        target_path = os.path.join(target_dir, phase_filename)
        if os.path.exists(target_path):
            try:
                os.remove(target_path)
                print(f"[Parser] Cleared existing phase file: {target_path}")
            except Exception as e:
                print(f"[Parser] Warning: could not remove {target_path}: {e}")

    # Write extracted files to disk without touching other phase files
    for filename, content in files_to_write.items():
        file_path = os.path.join(target_dir, filename)
        os.makedirs(os.path.dirname(file_path) or target_dir, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write((content or "").strip() + "\n")
        print(f"[Parser] Successfully wrote clean code to: {file_path}")

    return True


def execute_terraform_validation(target_dir="terraform_workspace"):
    """Run terraform init and validate non-interactively and return (success, output)."""

    print("[Validator] Initializing working directory...")
    init_res = subprocess.run(
        ["terraform", "init", "-input=false", "-no-color"],
        cwd=target_dir,
        capture_output=True,
        text=True,
    )

    if init_res.returncode != 0:
        return False, f"Initialization Failed:\n{init_res.stderr}\n{init_res.stdout}"

    print("[Validator] Running terraform validate...")
    val_res = subprocess.run(
        ["terraform", "validate", "-input=false", "-no-color"],
        cwd=target_dir,
        capture_output=True,
        text=True,
    )

    if val_res.returncode != 0:
        return False, f"Validation Failed:\n{val_res.stderr}\n{val_res.stdout}"

    return True, val_res.stdout
