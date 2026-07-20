import pytest
from src.nodes import routing_decision_router
from src.state import GraphState, create_initial_state

def test_routing_decision_router_priority():
    # Test priority: network.tf should be resolved first even if other files have errors
    state = GraphState(
        deployment_mode="new",
        user_prompt="test",
        aws_input_data={},
        retry_count=1,
        max_retries=3,
        current_phase="data",
        network_hcl="resource vpc",
        security_hcl="resource sg",
        compute_hcl="resource inst",
        data_hcl="resource db",
        validation_results="some error",
        is_valid=False,
        failing_files=["data.tf", "network.tf", "security.tf"],
        infrastructure_graph={"nodes": {}, "edges": []},
        compliance_report=[]
    )
    
    route = routing_decision_router(state)
    assert route == "fix_network"

def test_routing_decision_router_hierarchical():
    # Test fallback order: security, then compute, then data
    state = GraphState(
        deployment_mode="new",
        user_prompt="test",
        aws_input_data={},
        retry_count=1,
        max_retries=3,
        current_phase="data",
        network_hcl="resource vpc",
        security_hcl="resource sg",
        compute_hcl="resource inst",
        data_hcl="resource db",
        validation_results="some error",
        is_valid=False,
        failing_files=["data.tf", "security.tf"],
        infrastructure_graph={"nodes": {}, "edges": []},
        compliance_report=[]
    )
    
    route = routing_decision_router(state)
    assert route == "fix_security"
    
    # Compute error priority over data
    state["failing_files"] = ["data.tf", "compute.tf"]
    route = routing_decision_router(state)
    assert route == "fix_compute"
    
    # Only data fails
    state["failing_files"] = ["data.tf"]
    route = routing_decision_router(state)
    assert route == "fix_data"

def test_routing_decision_router_max_retries_exit():
    # If retry_count >= max_retries, it should return complete
    state = GraphState(
        deployment_mode="new",
        user_prompt="test",
        aws_input_data={},
        retry_count=3,
        max_retries=3,
        current_phase="compute",
        network_hcl="resource vpc",
        security_hcl="resource sg",
        compute_hcl="resource inst",
        data_hcl="",
        validation_results="error",
        is_valid=False,
        failing_files=["compute.tf"],
        infrastructure_graph={"nodes": {}, "edges": []},
        compliance_report=[]
    )
    
    route = routing_decision_router(state)
    assert route == "complete"
