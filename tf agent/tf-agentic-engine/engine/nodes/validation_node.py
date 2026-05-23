import os
import sys
from engine.state import GraphState
from engine.validator import execute_terraform_validation


# Support a dry-run flag via env or CLI
SKIP_VALIDATE = ("--skip-validate" in sys.argv) or (os.environ.get("SKIP_VALIDATE") == "1")


def validation_node_func(state: GraphState) -> dict:
    print("[Node] Running Validation...")

    if SKIP_VALIDATE:
        print("[Validator] SKIP_VALIDATE enabled; forcing success (dry-run).")
        return {"is_valid": True}

    success, output = execute_terraform_validation()
    if success:
        return {"is_valid": True}
    else:
        errors = state.get("error_logs", [])
        errors.append(output)
        retry = state.get("retry_count", 0) + 1
        print(f"[Validator] Validation failed. Incrementing retry_count -> {retry}")
        return {"is_valid": False, "error_logs": errors, "retry_count": retry}
