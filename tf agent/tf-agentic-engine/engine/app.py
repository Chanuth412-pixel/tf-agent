import json
import os
import ollama
from prompts import SYSTEM_PROMPT
from validator import parse_and_write_files, execute_terraform_validation

MAX_ITERATIONS = 5


def load_mock_infra(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_agentic_loop(infra_data):
    # Initialize the chat session context with the system prompt and original instructions
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Convert the following scanned infrastructure JSON to clean, explicit, static Terraform configurations:\n\n{json.dumps(infra_data, indent=2)}",
        },
    ]

    iteration = 1
    while iteration <= MAX_ITERATIONS:
        print(f"\n[Agent Loop] Execution Iteration {iteration}/{MAX_ITERATIONS}...")

        # Request generation from local model
        response = ollama.chat(
            model="qwen2.5-coder:1.5b",
            messages=messages,
        )

        raw_output = response["message"]["content"]
        print(f"\n----- Raw Output (Iteration {iteration}) -----")
        print(raw_output)

        # Step A: Attempt to split text stream and write files to disk
        parsed_successfully = parse_and_write_files(raw_output)

        if parsed_successfully:
            # Step B: Run native Terraform validation commands
            success, validation_message = execute_terraform_validation()

            if success:
                print(
                    f"\n[Agent Loop] Success! Infrastructure code validated successfully on iteration {iteration}."
                )
                return raw_output
            else:
                print(
                    f"\n[Agent Loop] Validation failed on iteration {iteration}. Preparing feedback payload..."
                )
                error_feedback = (
                    f"The previous HCL output resulted in compilation errors.\n"
                    f"Please review the explicit Terraform errors below, correct your mistakes, "
                    f"and rewrite the complete files using the structural file markers:\n\n"
                    f"{validation_message}"
                )
        else:
            error_feedback = (
                "Failed to parse your output. Ensure you strictly utilize the file markers: "
                "--- main.tf ---, --- variables.tf ---, and --- outputs.tf --- with no additional surrounding text."
            )

        # Append the failed output and the error logs to keep context stateful
        messages.append({"role": "assistant", "content": raw_output})
        messages.append({"role": "user", "content": error_feedback})

        iteration += 1

    print(
        f"\n[Agent Loop] Error: Maximum iteration threshold ({MAX_ITERATIONS}) reached without successful validation."
    )
    return None


if __name__ == "__main__":
    mock_path = os.path.join("scanner", "mock_infra.json")
    if os.path.exists(mock_path):
        data = load_mock_infra(mock_path)
        run_agentic_loop(data)
    else:
        print(f"Error: Missing mock file at target path: {mock_path}")
