// Network phase (generated)
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
