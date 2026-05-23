// Data phase (generated)
resource "aws_db_instance" "example_db" {
  allocated_storage    = var.db_allocated_storage
  engine               = "mysql"
  engine_version       = var.db_engine_version
  instance_class       = var.db_instance_class
  name                 = var.db_name
  username             = var.db_username
  password             = var.db_password
  db_subnet_group_name = aws_subnet.private_1.id
  vpc_security_group_ids = [aws_security_group.app_sg.id]
  tags = { Environment = var.environment, Owner = var.owner, ManagedBy = "LangGraph-Agent" }
}
