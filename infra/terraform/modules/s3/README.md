# s3 module — placeholder

S3 buckets for MLflow artifacts and the decision audit log. Both have
versioning, encryption (SSE-S3), and a public-access block (all four
flags `true`).

## Day 8 implementation plan

For each bucket in `var.buckets`:

- `aws_s3_bucket` (versioning enabled, SSE-S3, public-access block)
- Lifecycle rule: transition to S3-IA at 90 days; Glacier at 365 days
- Bucket policy granting `s3:GetObject`/`s3:PutObject` only to the
  matching IRSA role from `iam_irsa`

## Inputs (planned)

| Variable | Description |
|----------|-------------|
| `buckets` | Map `{logical_name => bucket_name}` |

## Outputs (planned)

| Output | Description |
|--------|-------------|
| `mlflow_bucket` | Name of the MLflow artifacts bucket |
| `decision_log_bucket` | Name of the decision audit log bucket |
| `bucket_arns` | Full ARN map for IAM policy attachment |
