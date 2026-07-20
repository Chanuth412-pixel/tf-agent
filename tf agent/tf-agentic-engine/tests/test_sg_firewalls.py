import os
import pytest
from src.utils import sanitize_security_group_names

def test_sanitize_security_group_names(tmp_path):
    # Create a dummy HCL file with an invalid sg- name attribute
    hcl_content = """
    resource "aws_security_group" "example" {
      name        = "sg-my-security-group"
      description = "Allow inbound traffic"
      vpc_id      = "vpc-123456"
    }
    """
    
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    hcl_file = workspace / "security.tf"
    hcl_file.write_text(hcl_content, encoding='utf-8')
    
    # Run the sanitizer
    sanitize_security_group_names(workspace_path=str(workspace))
    
    # Read the content back and verify it was replaced
    sanitized_content = hcl_file.read_text(encoding='utf-8')
    assert 'name        = "tsg-my-security-group"' in sanitized_content
    assert 'sg-' not in sanitized_content
