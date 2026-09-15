terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.60" }
  }
}

# Private bucket holding the Olist + DataCo CSVs. They're gitignored (194 MB,
# see docs/gap-analysis.md §3) so they never ship in the container image or
# the repo — upload them here once per environment and the `migrate` ECS task
# (via `scripts/sync_datasets.py`) pulls them down before seeding.
resource "aws_s3_bucket" "datasets" {
  bucket = "${var.name}-datasets-${data.aws_caller_identity.current.account_id}"
  tags   = var.tags
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket_versioning" "datasets" {
  bucket = aws_s3_bucket.datasets.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "datasets" {
  bucket                  = aws_s3_bucket.datasets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "datasets" {
  bucket = aws_s3_bucket.datasets.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "datasets" {
  bucket = aws_s3_bucket.datasets.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}
