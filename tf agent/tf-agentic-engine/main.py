from src.agent import app
from src.aws_client import test_fetcher_locally, fetch_live_infrastructure


def main():
    print("Initializing Multi-Mode IaC Engine...")
    
    # --- 1. Set the Deployment Mode ---
    # Change these variables to test the different use cases
    MODE = "import" # Change to "new", "import", or "clone"
    USER_PROMPT = "Create a highly available 3-tier VPC with 2 public and 2 private subnets."
    
    aws_data = {}
    
    # --- 2. Conditional Execution ---
    if MODE in ["import", "clone"]:
        print(f"[Agent] Mode: {MODE}. Fetching existing AWS infrastructure...")
        # Use test_fetcher_locally() for safe RAM testing, or fetch_live_infrastructure() for production
        aws_data = fetch_live_infrastructure() 
    elif MODE == "new":
        print(f"[Agent] Mode: {MODE}. Bypassing AWS fetch. Using user prompt.")
    else:
        print("Invalid deployment mode.")
        return

    # --- 3. Inject into State ---
    initial_state = {
        "deployment_mode": MODE,
        "user_prompt": USER_PROMPT,
        "aws_input_data": aws_data,
        "retry_count": 0,
        "max_retries": 3,
        "current_phase": "network",
        "infrastructure_graph": {"nodes": {}, "edges": []},
        "compliance_report": [],
    }

    # --- 4. Execute the Graph ---
    print(f"[Agent] Starting generation pipeline...")
    final_state = app.invoke(initial_state)
    
    print("\n--- FINAL STATE ---")
    print(f"Validation Passed: {final_state.get('is_valid')}")
    
    # ADD THIS TO PRINT THE ACTUAL TERRAFORM ERRORS
    if not final_state.get('is_valid'):
        print("\n--- VALIDATION ERRORS ---")
        for error in final_state.get('error_logs', []):
            print(error)


if __name__ == "__main__":
    main()
