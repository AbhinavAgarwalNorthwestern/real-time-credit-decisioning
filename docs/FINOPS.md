# FinOps — Cost Attribution Strategy

**Per FAANG Tier 2B of `scope_expansion_plan.md`.**

This project's AWS spend is attributable down to (product × environment ×
cost-center × model-name × team) via the standard tag set provisioned by
`infra/terraform/modules/tagging/`. Every Terraform resource consumes that
module's `tags` output.

## Tag schema

| Key | Allowed values | Used for |
|-----|---------------|----------|
| `product` | `credit-decisioning`, etc. | Top-level grouping; shown in AWS Cost Explorer |
| `environment` | `dev`, `staging`, `prod` | Per-env cost charts; enables blue/green cost comparison |
| `cost-center` | e.g. `ml-platform`, `infra` | Finance-side charge-back grouping |
| `model-name` | e.g. `credit_t_learner_v1.1.0`, or `shared` | Per-model spend (training + serving infra) |
| `team` | e.g. `credit-risk-ml` | Engineering ownership |
| `managed-by` | always `terraform` | Distinguishes IaC vs console-created resources |
| `repo` | always `realtime-credit-decisioning` | Cross-project disambiguation |

## OpenCost / Kubecost integration

The tag keys match the OpenCost AWS integration conventions
(<https://opencost.io/docs/configuration/aws>). Once OpenCost is installed
in the EKS cluster (via Helm chart), pod-level cost allocation
automatically picks up these tags via the AWS Cost & Usage Report (CUR)
exporter.

For Kubecost: same tags work; configure `kubecost-cost-analyzer` with
`aws.athenaProjectID` pointing to the CUR.

## Reports

Standard reports the model risk + finance teams need monthly:

1. **Per-model spend** — group by `model-name` tag. Shows training-pipeline
   cost (EKS compute + EMR + S3 storage) attributable to each MLflow model
   version.
2. **Per-environment spend** — split dev/staging/prod. Spot expensive
   dev experimentation.
3. **Unallocated spend** — resources with missing tags. **Goal: zero.**
   Untagged resources are an IaC bug — fix the Terraform module to add the tag.
4. **Top 10 resources by spend** — quick wins for cost optimization.

## Implementation

Apply the tagging module in every consumer:

```hcl
module "tags" {
  source       = "../../modules/tagging"
  product      = "credit-decisioning"
  environment  = "prod"
  cost_center  = "ml-platform"
  model_name   = var.model_name  # e.g., "credit_t_learner_v1.1.0"
  team         = "credit-risk-ml"
}

resource "aws_s3_bucket" "mlflow_artifacts" {
  bucket = "${var.product}-${var.environment}-mlflow"
  tags   = module.tags.tags
}
```

## Audit

CI should fail on Terraform plans that introduce resources without the
tagging module. Implement via Sentinel policy or `tflint` custom rule:

```hcl
# .tflint.hcl
rule "aws_resource_missing_tags" {
  enabled  = true
  tags     = ["product", "environment", "cost-center", "model-name", "team"]
}
```

The rule fails CI if any AWS resource type misses a required tag — preventing
the "untagged spend" creep that's the #1 FinOps failure mode at most banks.

## Why this matters for credit-decisioning specifically

Real banks must answer "what does each model version cost to operate?" for:
- **Model lifecycle review** — is this PD model worth its compute spend?
- **Charge-back to product teams** — credit-card business pays for its share
- **Capital planning** — operating costs flow into the bank's expense model
- **SR 11-7 governance** — model risk reviews include operational cost

Without per-model tagging, all of this is impossible.
