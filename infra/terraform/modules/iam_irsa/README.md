# iam_irsa module — placeholder

IRSA (IAM Roles for Service Accounts) mappings. Each pod that needs AWS
access gets a ServiceAccount with a role-arn annotation; EKS trades the
SA token for STS credentials at pod start. No long-lived AWS keys
embedded in K8s Secrets.

## Day 8 implementation plan

For each entry in `var.service_accounts`:

- `aws_iam_role` with a trust policy scoped to the cluster's OIDC
  provider + namespace + service-account name
- `aws_iam_role_policy_attachment` for the matching bucket-specific
  policy (e.g. MLflow SA gets RW on the MLflow artifacts bucket only)
- output: ServiceAccount manifest snippet to be consumed by the
  Kustomize overlay patch (`overlays/aws-eks/`)

## Inputs (planned)

| Variable | Description |
|----------|-------------|
| `cluster_oidc_provider_arn` | From the `eks_cluster` module |
| `service_accounts` | Map `{logical_name => {namespace, bucket_arn, ...}}` |

## Outputs (planned)

| Output | Description |
|--------|-------------|
| `role_arns` | Map `{logical_name => role ARN}` for SA annotation |
