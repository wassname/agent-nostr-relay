# Agent Nostr Relay — AWS deployment
# Simple: one EC2 instance, one security group, one Route53 record.
# Usage:
#   terraform init
#   terraform plan -var="domain=yourdomain.md" -var="subdomain=relay"
#   terraform apply -var="domain=yourdomain.md" -var="subdomain=relay"

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  required_version = ">= 1.0"
}

# ─── Variables ───────────────────────────────────────────────────────

variable "aws_region" {
  default = "us-east-1"  # matches ~/.aws/agent-relay.pem
}

variable "domain" {
  description = "Domain name (must already be registered, can be non-Route53)"
  type        = string
}

variable "subdomain" {
  description = "Subdomain for the relay (e.g. 'relay' → relay.yourdomain.md)"
  default     = "relay"
}

variable "instance_type_dev" {
  default = "t3.micro"  # 2 vCPU, 1GB RAM — $8.50/mo
}

variable "instance_type_prod" {
  default = "t3.small"  # 2 vCPU, 2GB RAM — $17/mo
}

variable "environment" {
  description = "dev or prod"
  default     = "dev"
}

variable "key_name" {
  description = "Name of the SSH key pair in AWS"
  type        = string
}

# ─── Provider ────────────────────────────────────────────────────────

provider "aws" {
  region = var.aws_region
}

# ─── Data: latest Ubuntu AMI ─────────────────────────────────────────

data "aws_ami" "ubuntu" {
  most_recent = true
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
  owners = ["099720109477"]  # Canonical
}

# ─── Security group ──────────────────────────────────────────────────

resource "aws_security_group" "relay" {
  name        = "agent-relay-${var.environment}"
  description = "Agent Nostr relay — HTTP, HTTPS, Nostr WS"

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]  # Consider restricting to your IP
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ─── EC2 instance ────────────────────────────────────────────────────

resource "aws_instance" "relay" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.environment == "prod" ? var.instance_type_prod : var.instance_type_dev
  key_name               = var.key_name
  vpc_security_group_ids = [aws_security_group.relay.id]
  user_data              = file("${path.module}/user-data.sh")

  root_block_device {
    volume_size = 50  # GB — enough for strfry LMDB + SQLite
    volume_type = "gp3"
  }

  tags = {
    Name        = "agent-relay-${var.environment}"
    Environment = var.environment
    Project     = "agent-nostr-relay"
  }
}

# ─── Route53 record (if you have a hosted zone) ─────────────────────

# Uncomment if your domain is managed by Route53:
# data "aws_route53_zone" "main" {
#   name         = var.domain
#   private_zone = false
# }
#
# resource "aws_route53_record" "relay" {
#   zone_id = data.aws_route53_zone.main.zone_id
#   name    = "${var.subdomain}.${var.domain}"
#   type    = "A"
#   ttl     = 300
#   records = [aws_instance.relay.public_ip]
# }

# ─── Outputs ─────────────────────────────────────────────────────────

output "instance_public_ip" {
  value = aws_instance.relay.public_ip
}

output "instance_public_dns" {
  value = aws_instance.relay.public_dns
}

output "ssh_command" {
  value = "ssh -i ${var.key_name}.pem ubuntu@${aws_instance.relay.public_dns}"
}
