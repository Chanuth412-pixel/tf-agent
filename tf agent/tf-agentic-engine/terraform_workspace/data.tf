data "aws_db_subnet_group" "private_subnets" {
  name = var.db_subnet_group_name
}

resource "aws_rds_instance" "db_instance" {
  engine         = "mysql"
  instance_class = var.instance_type
  allocated_storage = 20
  db_name        = var.db_name
  username      = var.db_username
  password      = var.db_password
  vpc_security_groups = [aws_security_group.private_sg.id]
  subnet_id       = data.aws_db_subnet_group.private_subnets.subnet_ids[0]

  tags = {
    Environment = "Production"
    Owner     = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}

resource "aws_s3_bucket" "db_logs" {
  bucket = var.db_logs_bucket_name
  acl    = "private"

  tags = {
    Environment = "Production"
    Owner     = "LangGraph-Agent"
    ManagedBy = "LangGraph-Agent"
  }
}
