resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_subnet" "public_1" {
  vpc_id     = aws_vpc.main.id
  cidr_block = "10.0.1.0/24"
}

locals {
  vpc_id     = aws_vpc.main.id
  subnet_ids = [aws_subnet.public_1.id]
}