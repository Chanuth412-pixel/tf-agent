// Compute phase (generated)
resource "aws_instance" "web" {
  ami           = var.ami_id
  instance_type = var.instance_type
  subnet_id     = aws_subnet.public_1.id
  vpc_security_group_ids = [aws_security_group.app_sg.id]
  tags = { Environment = var.environment, Owner = var.owner, ManagedBy = "LangGraph-Agent" }
}
