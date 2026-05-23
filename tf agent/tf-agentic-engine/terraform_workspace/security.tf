# Define the environment and owner variables from variables.tf
variable "environment" {
  type = string
}

variable "owner" {
  type = string
}

# Define the VPC ID using the aws_vpc.main.id attribute
locals {
  vpc_id = aws_vpc.main.id
}

# Define the security group for the VPC
resource "aws_security_group" "vpc_sg" {
  name        = "${var.environment}-vpc-sg"
  description = "Security group for ${var.environment} VPC"
  vpc_id     = local.vpc_id

  tags = {
    Environment = var.environment
    Owner      = var.owner
    ManagedBy = "LangGraph-Agent"
  }
}

# Define the network ACL for the VPC
resource "aws_network_acl" "vpc_nacl" {
  name        = "${var.environment}-vpc-nacl"
  description = "Network ACL for ${var.environment} VPC"
  vpc_id     = local.vpc_id

  tags = {
    Environment = var.environment
    Owner      = var.owner
    ManagedBy = "LangGraph-Agent"
  }
}
