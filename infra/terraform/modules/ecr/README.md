# ecr module — placeholder

One ECR repository per containerized service. Lifecycle policy keeps
the last N image versions to control storage cost.

## Day 8 implementation plan

For each service in `var.services`, create:

- `aws_ecr_repository` with image scanning on push
- `aws_ecr_lifecycle_policy` retaining last 30 tagged images + 7 days
  of untagged
- output a map `{service_name => repository_url}` consumed by the
  Kustomize overlay's image map

## Inputs (planned)

| Variable | Description |
|----------|-------------|
| `services` | List of service names |
| `image_scan_on_push` | bool (default `true`) |

## Outputs (planned)

| Output | Description |
|--------|-------------|
| `repository_urls` | Map `{service_name => repo URL}` |
