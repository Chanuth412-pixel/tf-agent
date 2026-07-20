import math
import re

class LocalVectorDB:
    def __init__(self):
        # Seeded exclusively with official Terraform AWS Provider schemas (v4/v5 compliant)
        self.documents = {
            "aws_s3_bucket": """
# Official AWS Provider S3 Bucket Configuration (v4/v5 compliant)
resource "aws_s3_bucket" "bucket" {
  bucket = "example-bucket"
}

resource "aws_s3_bucket_versioning" "versioning" {
  bucket = aws_s3_bucket.bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "sse" {
  bucket = aws_s3_bucket.bucket.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
""",
            "aws_db_instance": """
# Official AWS Provider Relational Database Service (RDS) Instance Schema
resource "aws_db_instance" "db" {
  allocated_storage      = 20
  db_name                = "mydb" # Note: Use db_name, NEVER name!
  engine                 = "postgres"
  engine_version         = "15.4"
  instance_class         = "db.t3.micro"
  username               = "dbadmin"
  password               = "securepassword123"
  db_subnet_group_name   = aws_db_subnet_group.subnet_group.name
  vpc_security_group_ids = [aws_security_group.sg.id]
  skip_final_snapshot    = true
}
""",
            "aws_dynamodb_table": """
# Official AWS Provider DynamoDB Table Schema (v4/v5 compliant)
resource "aws_dynamodb_table" "table" {
  name         = "example-table"
  billing_mode = "PAY_PER_REQUEST" # ALWAYS set billing_mode to PAY_PER_REQUEST when read/write capacities are omitted
  hash_key     = "UserId"

  attribute {
    name = "UserId"
    type = "S"
  }
}
""",
            "aws_db_subnet_group": """
# Official AWS Provider Database Subnet Group Schema
resource "aws_db_subnet_group" "subnet_group" {
  name       = "db-subnet-group"
  subnet_ids = [aws_subnet.private_1.id, aws_subnet.private_2.id]
}
""",
            "aws_sqs_queue": """
# Official AWS Provider Simple Queue Service (SQS) Schema
resource "aws_sqs_queue" "queue" {
  name                      = "example-queue"
  delay_seconds             = 90
  max_message_size          = 2048
  message_retention_seconds = 86400
  receive_wait_time_seconds = 10
}
""",
            "aws_lambda_function": """
# Official AWS Provider Lambda Function Schema
resource "aws_lambda_function" "lambda" {
  filename      = "lambda_function_payload.zip"
  function_name = "example_lambda"
  role          = aws_iam_role.iam_for_lambda.arn
  handler       = "index.handler"
  runtime       = "python3.11"
}
"""
        }

    def _tokenize(self, text):
        return re.findall(r'[a-zA-Z0-9_]+', text.lower())

    def _cosine_similarity(self, vec1, vec2):
        intersection = set(vec1.keys()) & set(vec2.keys())
        numerator = sum([vec1[x] * vec2[x] for x in intersection])

        sum1 = sum([val**2 for val in vec1.values()])
        sum2 = sum([val**2 for val in vec2.values()])
        denominator = math.sqrt(sum1) * math.sqrt(sum2)

        if not denominator:
            return 0.0
        return float(numerator) / denominator

    def _get_frequency_vector(self, tokens):
        vec = {}
        for token in tokens:
            vec[token] = vec.get(token, 0) + 1
        return vec

    def query(self, query_str: str, threshold: float = 0.1) -> str:
        query_vec = self._get_frequency_vector(self._tokenize(query_str))
        best_match = None
        best_score = 0.0

        for key, doc_text in self.documents.items():
            doc_vec = self._get_frequency_vector(self._tokenize(key + " " + doc_text))
            score = self._cosine_similarity(query_vec, doc_vec)
            if score > best_score:
                best_score = score
                best_match = doc_text

        if best_score >= threshold:
            return best_match
        return ""
