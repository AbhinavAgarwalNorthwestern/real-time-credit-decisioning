variable "cluster_oidc_provider_arn" {
  type = string
}

variable "cluster_oidc_issuer" {
  type = string
}

variable "mlflow_bucket" {
  type = string
}

variable "decision_log_bucket" {
  type = string
}

variable "shap_results_bucket" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  oidc_id = replace(var.cluster_oidc_issuer, "https://", "")

  service_accounts = {
    decisioner = {
      namespace = "real-time-ml"
      sa_name   = "decisioner"
      buckets   = [var.decision_log_bucket]
    }
    mlflow = {
      namespace = "mlflow"
      sa_name   = "mlflow"
      buckets   = [var.mlflow_bucket]
    }
    shap-consumer = {
      namespace = "real-time-ml"
      sa_name   = "shap-consumer"
      buckets   = [var.shap_results_bucket, var.decision_log_bucket]
    }
    drift-monitor = {
      namespace = "real-time-ml"
      sa_name   = "drift-monitor"
      buckets   = []
    }
    retraining-flow = {
      namespace = "real-time-ml"
      sa_name   = "retraining-flow"
      buckets   = [var.mlflow_bucket]
    }
  }
}

data "aws_iam_policy_document" "assume_role" {
  for_each = local.service_accounts

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"

    principals {
      type        = "Federated"
      identifiers = [var.cluster_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_id}:sub"
      values   = ["system:serviceaccount:${each.value.namespace}:${each.value.sa_name}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_id}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sa" {
  for_each = local.service_accounts

  name               = "rtcd-${each.key}-irsa"
  assume_role_policy = data.aws_iam_policy_document.assume_role[each.key].json

  tags = merge(var.tags, {
    Module         = "iam_irsa"
    ServiceAccount = each.key
  })
}

data "aws_iam_policy_document" "s3_access" {
  for_each = { for k, v in local.service_accounts : k => v if length(v.buckets) > 0 }

  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
      "s3:DeleteObject",
    ]
    resources = flatten([
      for bucket in each.value.buckets : [
        "arn:aws:s3:::${bucket}",
        "arn:aws:s3:::${bucket}/*",
      ]
    ])
  }
}

resource "aws_iam_role_policy" "s3_access" {
  for_each = { for k, v in local.service_accounts : k => v if length(v.buckets) > 0 }

  name   = "s3-access"
  role   = aws_iam_role.sa[each.key].id
  policy = data.aws_iam_policy_document.s3_access[each.key].json
}

output "role_arns" {
  value = { for k, v in aws_iam_role.sa : k => v.arn }
}
