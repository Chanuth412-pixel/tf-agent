"""LLM client abstraction for cloud-hosted or local fallbacks.

`call_cloud_llm` provides a single entrypoint for generating HCL. In CI or
when no API key is available, it falls back to a deterministic mock HCL
generator that respects `prompts` and `input_variables`.
"""
from typing import Dict
import os
from engine import prompts


def call_cloud_llm(prompt_template: str, input_variables: Dict) -> str:
    """Return a HCL fragment given a prompt template and inputs.

    If OPENROUTER_API_KEY is present, this function should be extended to
    call the remote LLM. For now, implement a safe local fallback which
    renders readable HCL using the rules in `prompts`.
    """
    # Simple deterministic fallbacks per phase prompt
    if prompt_template == prompts.NETWORK_PROMPT:
        return '''// Network phase (generated)
resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr
  description = "Primary VPC"
  tags = {
    Environment = var.environment
    Owner       = var.owner
    ManagedBy   = "LangGraph-Agent"
  }
}

resource "aws_subnet" "public_1" {
  vpc_id     = aws_vpc.main.id
  cidr_block = var.public_subnet_cidr
  availability_zone = var.availability_zone
  tags = { Environment = var.environment, Owner = var.owner, ManagedBy = "LangGraph-Agent" }
}

resource "aws_subnet" "private_1" {
  vpc_id     = aws_vpc.main.id
  cidr_block = var.private_subnet_cidr
  tags = { Environment = var.environment, Owner = var.owner, ManagedBy = "LangGraph-Agent" }
}
'''

    if prompt_template == prompts.SECURITY_PROMPT:
        return '''// Security phase (generated)
resource "aws_security_group" "app_sg" {
  name   = "app_sg"
  description = "Application security group"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Environment = var.environment, Owner = var.owner, ManagedBy = "LangGraph-Agent" }
}

resource "aws_iam_role" "app_role" {
  name = "example_app_role"
  assume_role_policy = jsonencode({ Statement = [] })
  tags = { Environment = var.environment, Owner = var.owner, ManagedBy = "LangGraph-Agent" }
}
'''

    if prompt_template == prompts.COMPUTE_PROMPT:
        return '''// Compute phase (generated)
resource "aws_instance" "web" {
  ami           = var.ami_id
  instance_type = var.instance_type
  subnet_id     = aws_subnet.public_1.id
  vpc_security_group_ids = [aws_security_group.app_sg.id]
  tags = { Environment = var.environment, Owner = var.owner, ManagedBy = "LangGraph-Agent" }
}
'''

    if prompt_template == prompts.DATA_PROMPT:
        return '''// Data phase (generated)
resource "aws_db_instance" "example_db" {
  allocated_storage    = var.db_allocated_storage
  engine               = "mysql"
  engine_version       = var.db_engine_version
  instance_class       = var.db_instance_class
  name                 = var.db_name
  username             = var.db_username
  password             = var.db_password
  db_subnet_group_name = aws_subnet.private_1.id
  vpc_security_group_ids = [aws_security_group.app_sg.id]
  tags = { Environment = var.environment, Owner = var.owner, ManagedBy = "LangGraph-Agent" }
}
'''

    # Default fallback: echo input for debugging
    return "// LLM fallback: no specific template matched."
