# Define the VPC using a local variable
locals {
  vpc_id = aws_vpc.main.id
}

# Create a security group for the VPC
resource "aws_security_group" "vpc_sg" {
  name        = "${var.environment}-vpc-sg"
  description = "Security group for the ${var.environment} VPC"
  vpc_id     = local.vpc_id

  tags = {
    Environment = var.environment
    Owner      = var.owner
    ManagedBy = "LangGraph-Agent"
  }
}

# Create a network ACL for the VPC
resource "aws_network_acl" "vpc_nacl" {
  name        = "${var.environment}-vpc-nacl"
  description = "Network ACL for the ${var.environment} VPC"
  vpc_id     = local.vpc_id

  tags = {
    Environment = var.environment
    Owner      = var.owner
    ManagedBy = "LangGraph-Agent"
  }
}
