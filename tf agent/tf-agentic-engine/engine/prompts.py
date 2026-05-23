"""Phase-specific prompt templates for the four-stage generator.

Each prompt enforces strict HCL readability rules: use `variables.tf`, add
standard tag blocks, and reference upstream resources by name (no hardcoded
IDs). The outputs must be valid HCL fragments suitable for writing into the
phase file named by the caller (e.g., `network.tf`).
"""

COMMON_RULES = """
CRITICAL RULES (apply to all phases):
1. Output ONLY valid HCL (no markdown, no prose).
2. Do NOT hardcode tunables: use variables from `variables.tf` (e.g., var.vpc_cidr, var.instance_type, var.ami_id, var.db_name).
3. Every resource must include a `tags` block with at least: Environment, Owner, ManagedBy = "LangGraph-Agent".
4. Reference upstream resources by resource address (e.g., `aws_vpc.main.id`, `aws_subnet.private_1.id`).
5. Add a short `description` argument on resources when applicable.
6. Keep blocks consistently spaced and group related resources.
"""

NETWORK_PROMPT = f"""
{COMMON_RULES}

Generate the NETWORK layer only. Produce resources for VPC, public and
private subnets, internet gateway, and route tables. Use variables for CIDR
values (e.g., var.vpc_cidr, var.public_subnet_cidr, var.private_subnet_cidr).
Name the VPC `aws_vpc.main` and subnets `aws_subnet.public_1` and
`aws_subnet.private_1` so downstream phases can reference them.

CRITICAL SYNTAX RULES:
1. When defining route tables, use the exact singular block `route {...}` (do NOT use `routes`).
2. For public internet access routes, set the `cidr_block` explicitly to "0.0.0.0/0".
3. Declare variables and locals only in `variables.tf` or inside a single `locals {...}` block — do NOT emit naked assignments at top-level.
4. Ensure resource names are stable and deterministic (e.g., `aws_vpc.main`, `aws_subnet.public_1`).
"""

SECURITY_PROMPT = f"""
{COMMON_RULES}

Generate the SECURITY layer only: security groups, network ACLs, IAM roles
and policies. Use the provided `network_context` to reference `aws_vpc.main.id`
and subnet resources. Ensure security groups reference `aws_vpc.main.id` and
attach the standard `tags` block using `var.environment` and `var.owner`.

CRITICAL SYNTAX RULES:
1. Do NOT redefine or redeclare the `resource "aws_vpc" "main"` block — it must exist only in `network.tf`.
2. Always reference the VPC using the exact attribute `aws_vpc.main.id`.
3. Do NOT emit naked top-level assignments; if local values are required wrap them inside `locals {...}`.
4. Avoid duplicating security group names between runs; use fixed resource addressing.
"""

COMPUTE_PROMPT = f"""
{COMMON_RULES}

Generate the COMPUTE layer only: EC2 instances, launch templates, and
auto-scaling groups. Reference `aws_subnet.public_1.id` or `aws_subnet.private_1.id`
as appropriate and reference security groups by resource address. Use
`var.instance_type` and `var.ami_id` rather than hardcoding values.
Include placements across subnets as necessary.

CRITICAL SYNTAX RULES:
1. Do NOT redefine the VPC or any Security Groups — reference them by resource address only.
2. Attach instances to subnets using `subnet_id = aws_subnet.public_1.id` (do not hardcode strings).
3. Do NOT reference resources that are not declared (e.g., `aws_key_pair`), unless explicitly created within this compute phase.
"""

DATA_PROMPT = f"""
{COMMON_RULES}

Generate the DATA layer only: RDS instances, DB subnet groups, and S3
buckets. Place any databases in private subnets and reference security groups
from the security phase. Use `var.db_name`, `var.db_username`, `var.db_password`
via variables (avoid plaintext credentials in HCL files; allow variables to
be set externally).
"""
