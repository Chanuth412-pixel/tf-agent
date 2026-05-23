provider "aws" {
  region = var.region
}

resource "aws_instance" "legacy_web_app" {
  ami           = var.attributes.ami
  instance_type = var.attributes.instance_type
  availability_zone = var.attributes.availability_zone

  tags = {
    Name = var.name
  }
}
