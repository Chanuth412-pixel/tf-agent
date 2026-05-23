# Network Phase
resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr

  tags = {
    Environment = "Production"
    Owner     = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

resource "aws_subnet" "public_1" {
  vpc_id   = aws_vpc.main.id
  cidr_block = var.public_subnet_cidr

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

# Compute Phase
resource "aws_instance" "web_server" {
  ami           = aws_ami.latest_amazon_linux2
  instance_type = var.instance_type
  subnet_id      = aws_subnet.public_1.id

  tags = {
    Environment = "Production"
    Owner     = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

resource "aws_instance" "database_server" {
  ami           = aws_ami.latest_amazon_linux2
  instance_type = var.instance_type
  subnet_id      = aws_subnet.private_1.id

  tags = {
    Environment = "Production"
    Owner     = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

# Auto-Scaling Group Phase
resource "aws_autoscaling_group" "web_server_asg" {
  ami           = aws_ami.latest_amazon_linux2
  instance_type = var.instance_type
  subnet_id      = aws_subnet.public_1.id

  tags = {
    Environment = "Production"
    Owner     = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }

  launch_configuration = aws_launch_configuration.web_server_lc.id

  target_group_arns = [aws_lb.target_group.main.arn]

  health_check {
    type     = "ELB"
    interval = 30
    timeout = 5
    healthy_threshold = 2
    unhealthy_threshold = 2
  }

  autoscaling_policy {
    name        = "ScaleOutPolicy"
    type        = "SimpleScaling"
    adjustment_type = "ChangeInCapacity"
    change_step_size = 1

    min_capacity = 1
    max_capacity = 5
  }
}

resource "aws_autoscaling_group" "database_server_asg" {
  ami           = aws_ami.latest_amazon_linux2
  instance_type = var.instance_type
  subnet_id      = aws_subnet.private_1.id

  tags = {
    Environment = "Production"
    Owner     = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }

  launch_configuration = aws_launch_configuration.database_server_lc.id

  target_group_arns = [aws_lb.target_group.main.arn]

  health_check {
    type     = "ELB"
    interval = 30
    timeout = 5
    healthy_threshold = 2
    unhealthy_threshold = 2
  }

  autoscaling_policy {
    name        = "ScaleOutPolicy"
    type        = "SimpleScaling"
    adjustment_type = "ChangeInCapacity"
    change_step_size = 1

    min_capacity = 1
    max_capacity = 5
  }
}
