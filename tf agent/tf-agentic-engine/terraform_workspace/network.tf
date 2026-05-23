# VARIABLES
variable "vpc_cidr" {
  type = string
  default = "10.0.0.0/16"
}

variable "public_subnet_cidr" {
  type = string
  default = "10.0.1.0/24"
}

variable "private_subnet_cidr" {
  type = string
  default = "10.0.2.0/24"
}

# LOCALS
locals {
  vpc_name = "LangGraph-Agent-VPC"
  public_subnet_name = "Public-Subnet"
  private_subnet_name = "Private-Subnet"
}

# VPC
resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr

  tags = {
    Environment = "Production"
    Owner      = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }

  description = "Main VPC for LangGraph-Agent"
}

# PUBLIC SUBNET
resource "aws_subnet" "public_1" {
  vpc_id     = aws_vpc.main.id
  cidr_block = var.public_subnet_cidr

  tags = {
    Environment = "Production"
    Owner      = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }

  description = "Public Subnet for LangGraph-Agent"
}

# PRIVATE SUBNET
resource "aws_subnet" "private_1" {
  vpc_id     = aws_vpc.main.id
  cidr_block = var.private_subnet_cidr

  tags = {
    Environment = "Production"
    Owner      = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }

  description = "Private Subnet for LangGraph-Agent"
}

# INTERNET GATEWAY
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Environment = "Production"
    Owner      = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }

  description = "Internet Gateway for LangGraph-Agent"
}

# ROUTE TABLE
resource "aws_route_table" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Environment = "Production"
    Owner      = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }

  description = "Main Route Table for LangGraph-Agent"

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}
