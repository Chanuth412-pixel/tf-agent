// Security phase (generated)
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
