# Define the VPC and Security Groups
resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr
  tags = {
    Environment = "Production"
    Owner = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

resource "aws_security_group" "public" {
  vpc_id = aws_vpc.main.id
  name = "Public-Security-Group"
  description = "Allow HTTP and HTTPS traffic"
  ingress {
    from_port = 80
    to_port = 80
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port = 443
    to_port = 443
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "private" {
  vpc_id = aws_vpc.main.id
  name = "Private-Security-Group"
  description = "Allow SSH traffic"
  ingress {
    from_port = 22
    to_port = 22
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Define the EC2 instances
resource "aws_instance" "web_server" {
  ami_id = var.ami_id
  instance_type = var.instance_type
  subnet_id = aws_subnet.public_1.id
  tags = {
    Environment = "Production"
    Owner = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

resource "aws_instance" "database_server" {
  ami_id = var.ami_id
  instance_type = var.instance_type
  subnet_id = aws_subnet.private_1.id
  tags = {
    Environment = "Production"
    Owner = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

# Define the Auto-Scaling Group for the Web Server
resource "aws_autoscaling_group" "web_server_asg" {
  name = "Web-Server-Auto-Scaling-Group"
  launch_configuration = aws_launch_configuration.web_server_lc.id
  target_group_arns = [aws_lb.target_group.main.arn]
  min_size = 2
  max_size = 5
  health_check_type = "ELB"
  tags = {
    Environment = "Production"
    Owner = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

# Define the Launch Configuration for the Web Server
resource "aws_launch_configuration" "web_server_lc" {
  image_id = var.ami_id
  instance_type = var.instance_type
  security_groups = [aws_security_group.public.id]
  key_name = aws_key_pair.main.name
  tags = {
    Environment = "Production"
    Owner = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

# Define the Auto-Scaling Group for the Database Server
resource "aws_autoscaling_group" "database_server_asg" {
  name = "Database-Server-Auto-Scaling-Group"
  launch_configuration = aws_launch_configuration.database_server_lc.id
  target_group_arns = [aws_lb.target_group.main.arn]
  min_size = 1
  max_size = 3
  health_check_type = "ELB"
  tags = {
    Environment = "Production"
    Owner = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

# Define the Launch Configuration for the Database Server
resource "aws_launch_configuration" "database_server_lc" {
  image_id = var.ami_id
  instance_type = var.instance_type
  security_groups = [aws_security_group.private.id]
  key_name = aws_key_pair.main.name
  tags = {
    Environment = "Production"
    Owner = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

# Define the Load Balancer for the Web Server and Database Server
resource "aws_lb" "main" {
  name = "Web-Server-LB"
  subnets = [aws_subnet.public_1.id, aws_subnet.private_1.id]
  security_groups = [aws_security_group.public.id, aws_security_group.private.id]
  tags = {
    Environment = "Production"
    Owner = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}
