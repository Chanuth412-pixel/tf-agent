from engine.state import GraphState


def routing_decision_router(state: GraphState) -> str:
    """Route based on validation outcome and retry limits.

    Returns one of: 'fix_network', 'fix_security', 'fix_compute', 'fix_data', or 'complete'
    """
    if state.get("is_valid"):
        return "complete"

    if state.get("retry_count", 0) >= state.get("max_retries", 3):
        print(f"[Router] Max retries ({state.get('max_retries')}) reached. Forcing exit.")
        return "complete"

    phase = state.get("current_phase", "network")
    return f"fix_{phase}"
