# vpc module — placeholder

VPC + public/private subnets across ≥2 AZs sized for the EKS cluster.

## Day 8 implementation plan

Use the upstream `terraform-aws-modules/vpc/aws` community module rather
than rolling our own. The community module handles:

- public/private subnet pairs per AZ
- NAT gateway placement (single-NAT for cost; multi-NAT for HA)
- correct subnet tagging for `kubernetes.io/cluster/<name>` + ALB ingress
- VPC endpoints for S3 + ECR (cuts NAT bandwidth + cost meaningfully)

## Inputs (planned)

| Variable | Description |
|----------|-------------|
| `cidr_block` | VPC CIDR (default `10.0.0.0/16`) |
| `azs` | List of AZ names (typically 2 for cost, 3 for HA) |
| `cluster_name` | Used for subnet tagging |

## Outputs (planned)

| Output | Description |
|--------|-------------|
| `id` | VPC ID |
| `public_subnet_ids` | For ALB |
| `private_subnet_ids` | For EKS node groups |
