import pytest
from src.vector_db import LocalVectorDB
from src.nodes import generate_data_node
from src.state import GraphState

def test_vector_db_query():
    db = LocalVectorDB()
    # Check that querying for aws_s3_bucket retrieves the S3 schema template
    s3_schema = db.query("aws_s3_bucket")
    assert "aws_s3_bucket" in s3_schema
    assert "aws_s3_bucket_versioning" in s3_schema
    
    # Check that querying for aws_db_instance retrieves db_name instruction
    db_schema = db.query("aws_db_instance")
    assert "aws_db_instance" in db_schema
    assert "db_name" in db_schema

def test_vector_db_query_miss():
    db = LocalVectorDB()
    # Check that a query with no relevant matches returns empty string
    schema = db.query("random_nonexistent_resource_type", threshold=0.9)
    assert schema == ""
