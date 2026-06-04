# `overlays/aws-eks/` — AWS EKS production-style deployment

Patches the cloud-agnostic `base/` for an AWS EKS cluster provisioned by
`infra/terraform/`:

- **Images** point at ECR (`<account>.dkr.ecr.<region>.amazonaws.com/<service>:<tag>`)
- **Storage class** is `gp3` (EBS CSI driver, provisioned by Terraform)
- **Replicas** scale up (typical 3 for stateless services, HPA-driven)
- **Resource limits** match production envelopes
- **S3** replaces MinIO; pods use IRSA (IAM Roles for Service Accounts) to access
  buckets — no embedded credentials
- **MLflow** Deployment is patched to remove the `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY` env vars (MinIO Secret) and instead use a
  ServiceAccount annotated with the IRSA role provisioned by Terraform
- **Ingress** uses the AWS Load Balancer Controller (ALB)

See `docs/AWS_DEPLOYMENT.md` for the full deployment walkthrough.

## Prerequisites

1. `infra/terraform/` applied — produces the EKS cluster, ECR repos, S3
   buckets, IAM roles, IRSA role for the MLflow ServiceAccount
2. `aws-cli` configured with the AWS account
3. `kubectl` config pointed at the EKS cluster:
   `aws eks update-kubeconfig --region <region> --name <cluster-name>`
4. Docker images built and pushed to ECR:
   `just docker-build-all aws-prod && just docker-push-all aws-prod`

## Apply

```bash
just k8s-apply-aws
# or directly:
kubectl apply -k deployments/overlays/aws-eks
```

## What this overlay is NOT

- It is **not** Terraform — Terraform provisions the cluster + supporting
  resources; this overlay deploys our application *into* that cluster
- It is **not** a Helm chart — third-party operators (Strimzi, RisingWave,
  if used) install via their own Helm releases independently
