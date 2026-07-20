import pytest
from src.utils import generate_reflection_context

def test_generate_reflection_context():
    failing_file = "compute.tf"
    raw_errors = "Error: Unsupported attribute"
    
    context = generate_reflection_context(failing_file, raw_errors)
    
    assert "[CRITICAL CORRECTION REQUIRED]" in context
    assert failing_file in context
    assert raw_errors in context
    assert "Identify the invalid properties or hallucinated argument bindings" in context
    assert "Do NOT invent attributes" in context
