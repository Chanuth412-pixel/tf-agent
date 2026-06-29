resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr

  tags = merge(
    var.tags,
    {
      Name = "Main VPC"
    }
  )
}

resource "aws_security_group" "main" {
  vpc_id = aws_vpc.main.id
  name   = "Main Security Group"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_subnet" "main" {
  vpc_id            = aws_vpc.main.id
  availability_zone = var.availability_zone
  cidr_block        = var.subnet_cidr

  tags = merge(
    var.tags,
    {
      Name = "Main Subnet"
    }
  )
}

resource "aws_autoscaling_group" "main" {
  vpc_zone_identifier = [var.subnet_id]

  launch_template {
    id = aws_launch_template.main.id
  }

  tags = merge(
    var.tags,
    {
      Name = "Main Auto Scaling Group"
    }
  )
}

resource "aws_dynamodb_table" "main" {
  name         = "MyDynamoDBTable"
  billing_mode = "PAY_PER_REQUEST"

  tags = merge(
    var.tags,
    {
      Name = "Main DynamoDB Table"
    }
  )
}

resource "aws_iam_role" "main" {
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = merge(
    var.tags,
    {
      Name = "Main IAM Role"
    }
  )
}

resource "aws_s3_bucket" "main" {
  bucket = var.s3_bucket_name

  tags = merge(
    var.tags,
    {
      Name = "Main S3 Bucket"
    }
  )
}