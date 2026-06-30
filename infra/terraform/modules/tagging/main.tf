# FinOps cost-attribution tagging — FAANG Tier 2B.
#
# Every AWS resource provisioned by this project gets these standard tags so
# the AWS Cost Allocation Report (and downstream OpenCost / Kubecost) can
# attribute spend to:
#   - product (which business product owns this)
#   - environment (dev / staging / prod)
#   - cost-center (which finance owner)
#   - model-name (which ML model this resource serves)
#   - managed-by (terraform)
#   - team (engineering team owner)
#
# OpenCost / Kubecost compatible — the tag keys match the conventions
# documented at https://opencost.io/docs/configuration/aws .
#
# Usage in caller modules:
#   module "tags" {
#     source       = "../modules/tagging"
#     product      = "credit-decisioning"
#     environment  = "prod"
#     cost_center  = "ml-platform"
#     model_name   = "credit_t_learner_v1.1.0"
#     team         = "credit-risk-ml"
#   }
#
#   resource "aws_s3_bucket" "data" {
#     bucket = "..."
#     tags   = module.tags.tags
#   }
#
# Critical: all resources MUST get the tags. Resources without these tags
# show up in the AWS Cost Explorer as "untagged" — invisible to FinOps.

variable "product" {
  description = "Product owning this resource (e.g., 'credit-decisioning')"
  type        = string
}

variable "environment" {
  description = "Environment: dev, staging, prod"
  type        = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging, or prod"
  }
}

variable "cost_center" {
  description = "Finance cost-center for charge-back (e.g., 'ml-platform')"
  type        = string
}

variable "model_name" {
  description = "ML model this resource serves (or 'shared' for infra)"
  type        = string
  default     = "shared"
}

variable "team" {
  description = "Engineering team owner"
  type        = string
  default     = "credit-risk-ml"
}

variable "extra_tags" {
  description = "Additional per-resource tags merged with the standard set"
  type        = map(string)
  default     = {}
}

locals {
  standard_tags = {
    "product"      = var.product
    "environment"  = var.environment
    "cost-center"  = var.cost_center
    "model-name"   = var.model_name
    "team"         = var.team
    "managed-by"   = "terraform"
    "repo"         = "realtime-credit-decisioning"
  }
  tags = merge(local.standard_tags, var.extra_tags)
}

output "tags" {
  description = "Merged tag map — pass to any AWS resource's tags = attribute"
  value       = local.tags
}

output "tag_keys" {
  description = "Key list — useful for IAM tag-based policy authoring"
  value       = keys(local.tags)
}
