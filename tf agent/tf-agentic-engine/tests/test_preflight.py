import os
import shutil
import tempfile
import pytest

from src.utils import parse_dependencies, find_missing_references, detect_cycles


def test_detect_cycles():
    # Simple DAG (no cycles)
    dep_map_clean = {
        "aws_subnet.public_1": ["aws_vpc.main"],
        "aws_instance.app": ["aws_subnet.public_1", "aws_security_group.main"],
        "aws_security_group.main": ["aws_vpc.main"],
        "aws_vpc.main": [],
    }
    assert detect_cycles(dep_map_clean) == []

    # Cyclic DAG (direct circular dependency)
    dep_map_cyclic = {
        "aws_subnet.public_1": ["aws_instance.app"],
        "aws_instance.app": ["aws_subnet.public_1"],
    }
    cycles = detect_cycles(dep_map_cyclic)
    assert len(cycles) > 0
    assert "aws_subnet.public_1" in cycles[0] or "aws_instance.app" in cycles[0]


def test_parse_dependencies_and_missing_refs():
    # Create a temporary directory for testing
    tmpdir = tempfile.mkdtemp()
    try:
        # 1. Write mock tf files
        # network.tf
        network_content = """
        variable "vpc_cidr" {
          default = "10.0.0.0/16"
        }
        resource "aws_vpc" "main" {
          cidr_block = var.vpc_cidr
        }
        resource "aws_subnet" "public_1" {
          vpc_id = aws_vpc.main.id
        }
        """
        # security.tf
        security_content = """
        resource "aws_security_group" "web" {
          vpc_id = aws_vpc.main.id
        }
        """
        # compute.tf (references missing security group and missing variable)
        compute_content = """
        resource "aws_instance" "web" {
          subnet_id = aws_subnet.public_1.id
          vpc_security_group_ids = [aws_security_group.web.id, aws_security_group.missing.id]
          instance_type = var.nonexistent_var
        }
        """

        with open(os.path.join(tmpdir, "network.tf"), "w", encoding="utf-8") as f:
            f.write(network_content)
        with open(os.path.join(tmpdir, "security.tf"), "w", encoding="utf-8") as f:
            f.write(security_content)
        with open(os.path.join(tmpdir, "compute.tf"), "w", encoding="utf-8") as f:
            f.write(compute_content)

        # 2. Parse dependencies
        dep_map = parse_dependencies(tmpdir)
        
        # Verify resources are extracted
        assert "aws_vpc.main" in dep_map
        assert "aws_subnet.public_1" in dep_map
        assert "aws_security_group.web" in dep_map
        assert "aws_instance.web" in dep_map
        assert "var.vpc_cidr" in dep_map

        # Verify dependency relationships
        assert "aws_vpc.main" in dep_map["aws_subnet.public_1"]
        assert "aws_vpc.main" in dep_map["aws_security_group.web"]
        assert "aws_subnet.public_1" in dep_map["aws_instance.web"]
        assert "aws_security_group.web" in dep_map["aws_instance.web"]

        # 3. Check for missing references
        missing_errors = find_missing_references(tmpdir, dep_map)
        
        # We expect two missing references: aws_security_group.missing and var.nonexistent_var
        assert len(missing_errors) == 2
        
        any_missing_sg = any("aws_security_group.missing" in err for err in missing_errors)
        any_missing_var = any("var.nonexistent_var" in err for err in missing_errors)
        
        assert any_missing_sg
        assert any_missing_var

    finally:
        shutil.rmtree(tmpdir)
