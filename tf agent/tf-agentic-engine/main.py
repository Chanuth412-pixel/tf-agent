from src.agent import app
from src.aws_client import fetch_live_infrastructure, test_fetcher_locally


def main():
    print("Initializing Modular LangGraph Engine...")
    
    # 1. Fetch the data dynamically from AWS
    print("[Agent] Fetching infrastructure state from AWS...")
    
    # NOTE: To test this locally without real AWS credentials, 
    # you can temporarily import and call `test_fetcher_locally()` / 'fetch_live_infrastructure()'
    aws_data = test_fetcher_locally()
    
    # 2. Inject it into the graph state
    initial_state = {
        "aws_input_data": aws_data,
        "retry_count": 0,
        "max_retries": 5,
        "current_phase": "network"
    }

    # 3. Execute the graph
    print(f"[Agent] Starting generation for VPC: {aws_data.get('vpc_id')}")
    final_state = app.invoke(initial_state)
    
    print("--- FINAL STATE ---")
    print(f"Validation Passed: {final_state.get('is_valid')}")
    print(f"Retries Used: {final_state.get('retry_count')}")


if __name__ == "__main__":
    main()
