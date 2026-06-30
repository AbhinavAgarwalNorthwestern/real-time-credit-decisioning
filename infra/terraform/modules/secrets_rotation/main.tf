# Terraform module for secret rotation in AWS Secrets Manager.
#
# Provisions one Secret per app, plus a Lambda function configured to rotate
# the secret every 90 days. The Lambda uses AWS's standard rotation pattern
# (4-step: createSecret → setSecret → testSecret → finishSecret).
#
# Pair this with the ExternalSecret resources in
# `deployments/overlays/aws-eks/external-secrets-store.yaml` — those consume
# what this module provisions.

variable "secret_name" {
  description = "Path of the secret in AWS Secrets Manager (e.g., prod/credit-decisioning/mlflow-minio)"
  type        = string
}

variable "rotation_lambda_arn" {
  description = "ARN of the Lambda that performs the actual rotation logic"
  type        = string
}

variable "automatically_after_days" {
  description = "Rotate every N days"
  type        = number
  default     = 90
}

variable "tags" {
  description = "Standard tagging — cost-center, model-name, environment"
  type        = map(string)
  default     = {}
}

resource "aws_secretsmanager_secret" "this" {
  name                    = var.secret_name
  description             = "Auto-rotated secret managed by External Secrets Operator + Lambda rotation"
  recovery_window_in_days = 7  # short window in dev; production may want 30

  tags = merge(var.tags, {
    "managed-by"  = "terraform"
    "rotation"    = "automatic"
    "module"      = "secrets_rotation"
  })
}

resource "aws_secretsmanager_secret_rotation" "this" {
  secret_id           = aws_secretsmanager_secret.this.id
  rotation_lambda_arn = var.rotation_lambda_arn

  rotation_rules {
    automatically_after_days = var.automatically_after_days
  }
}

output "secret_arn" {
  description = "ARN of the rotated secret — consume via ExternalSecret.remoteRef.key"
  value       = aws_secretsmanager_secret.this.arn
}

output "secret_name" {
  description = "Name (path) of the rotated secret"
  value       = aws_secretsmanager_secret.this.name
}
