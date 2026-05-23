variable "region" {
  type = string
}

variable "name" {
  type = string
}

variable "attributes" {
  type = map(string)
}

variable "environment" {
  type    = string
  default = "Production"
}

variable "owner" {
  type    = string
  default = "team@example.com"
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "public_subnet_cidr" {
  type    = string
  default = "10.0.2.0/24"
}

variable "private_subnet_cidr" {
  type    = string
  default = "10.0.1.0/24"
}

variable "instance_type" {
  type    = string
  default = "t3.micro"
}

variable "ami_id" {
  type    = string
  default = "ami-0c55b159cbfafe1f0"
}

variable "db_name" {
  type    = string
  default = "exampledb"
}

variable "db_username" {
  type    = string
  default = "admin"
}

variable "db_password" {
  type    = string
  default = "changeme"
}
