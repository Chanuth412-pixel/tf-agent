# Define variables for CIDR values
locals {
  vpc_cidr     = var.vpc_cidr
  public_subnet_cidr = var.public_subnet_cidr
  private_subnet_cidr = var.private_subnet_cidr
}

# Create the VPC
resource "aws_vpc" "main" {
  cidr_block       = local.vpc_cidr
  enable_dns_hostnames = true
  tags = {
    Environment = "Production"
    Owner      = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

# Create the public subnet
resource "aws_subnet" "public_1" {
  vpc_id     = aws_vpc.main.id
  cidr_block       = local.public_subnet_cidr
  availability_zone = var.availability_zone_public
  tags = {
    Environment = "Production"
    Owner      = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

# Create the private subnet
resource "aws_subnet" "private_1" {
  vpc_id     = aws_vpc.main.id
  cidr_block       = local.private_subnet_cidr
  availability_zone = var.availability_zone_private
  tags = {
    Environment = "Production"
    Owner      = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

# Create the internet gateway
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags = {
    Environment = "Production"
    Owner      = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

# Create the route table for the public subnet
resource "aws_route_table" "public_1" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Environment = "Production"
    Owner      = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

# Create the route table for the private subnet
resource "aws_route_table" "private_1" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Environment = "Production"
    Owner      = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}
