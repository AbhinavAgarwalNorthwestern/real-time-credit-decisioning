variable "environment" {
  type    = string
  default = "prod"
}

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  buckets = {
    mlflow_artifacts = "rtcd-mlflow-artifacts-${var.environment}"
    decision_log     = "rtcd-decision-log-${var.environment}"
    shap_results     = "rtcd-shap-results-${var.environment}"
  }
}

resource "aws_s3_bucket" "bucket" {
  for_each = local.buckets
  bucket   = each.value

  tags = merge(var.tags, {
    Module = "s3"
    Usage  = each.key
  })
}

resource "aws_s3_bucket_versioning" "bucket" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.bucket[each.key].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "bucket" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.bucket[each.key].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "bucket" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.bucket[each.key].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "decision_log" {
  bucket = aws_s3_bucket.bucket["decision_log"].id

  rule {
    id     = "archive-old-decisions"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 365
      storage_class = "GLACIER"
    }
    # 7-year retention for regulatory compliance (ECOA/Reg B)
    expiration {
      days = 2555
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "shap_results" {
  bucket = aws_s3_bucket.bucket["shap_results"].id

  rule {
    id     = "archive-shap"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 365
      storage_class = "GLACIER"
    }
    # 7-year retention for adverse-action reason codes
    expiration {
      days = 2555
    }
  }
}

output "mlflow_bucket" {
  value = aws_s3_bucket.bucket["mlflow_artifacts"].bucket
}

output "decision_log_bucket" {
  value = aws_s3_bucket.bucket["decision_log"].bucket
}

output "shap_results_bucket" {
  value = aws_s3_bucket.bucket["shap_results"].bucket
}
