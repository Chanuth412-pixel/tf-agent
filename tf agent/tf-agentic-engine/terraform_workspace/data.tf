# Network Phase
resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr

  tags = {
    Environment = "Production"
    Owner     = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

resource "aws_subnet" "private_1" {
  vpc_id   = aws_vpc.main.id
  cidr_block = var.private_subnet_cidr

  tags = {
    Environment = "Production"
    Owner     = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

resource "aws_subnet" "private_2" {
  vpc_id   = aws_vpc.main.id
  cidr_block = var.private_subnet_cidr

  tags = {
    Environment = "Production"
    Owner     = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

# Database Phase
resource "aws_db_instance" "main" {
  engine         = "mysql"
  instance_class = var.instance_type
  allocated_storage = 20
  username       = var.db_username
  password       = var.db_password

  db_name = var.db_name

  tags = {
    Environment = "Production"
    Owner     = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

# S3 Phase
resource "aws_s3_bucket" "main" {
  bucket        = var.s3_bucket_name
  acl          = "private"
  versioning   = true

  tags = {
    Environment = "Production"
    Owner     = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}
