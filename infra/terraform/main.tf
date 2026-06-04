# Realtime Credit-Decisioning Platform — AWS infrastructure root module.
#
# Provisions everything OUTSIDE the K8s cluster:
#   - VPC + public/private subnets across 2 AZs
#   - EKS cluster (managed control plane)
#   - ECR repositories per containerized service
#   - S3 buckets (MLflow artifacts + decision audit log)
#   - IAM roles and IRSA service-account mappings
#   - (optional) MLflow tracking server on EC2 if not running in-cluster
#
# K8s workloads (Deployments, Services, ConfigMaps) are NOT in this
# Terraform — they live in `deployments/overlays/aws-eks/` and apply via
# `kubectl apply -k`.
#
# See docs/decisions/006-base-overlays-kustomize-not-helm.md for the
# Terraform / Kustomize split, and docs/repo_layout.md for the broader
# infra/terraform/ + infra/lib/ structure.

terraform {
  required_version = ">= 1.7"
}

module "vpc" {
  source = "./modules/vpc"
  # cidr_block = "10.0.0.0/16"
  # azs        = ["${var.aws_region}a", "${var.aws_region}b"]
  # cluster_name = var.cluster_name
}

module "eks_cluster" {
  source = "./modules/eks_cluster"
  # vpc_id          = module.vpc.id
  # subnet_ids      = module.vpc.private_subnet_ids
  # cluster_name    = var.cluster_name
  # cluster_version = var.cluster_version
  # environment     = var.environment
}

module "ecr" {
  source = "./modules/ecr"
  # services = [
  #   "decisioner",
  #   "transactions",
  #   "behavioral_features",
  #   "drift_monitor",
  #   "outcome_collector",
  #   # crypto pipeline still images
  #   "trades",
  #   "candles",
  #   "technical_indicators",
  #   "news-ingestor",
  #   "prediction-generator",
  #   "prediction-api",
  #   "training-pipeline",
  # ]
}

module "s3" {
  source = "./modules/s3"
  # buckets = {
  #   mlflow_artifacts = "mlflow-artifacts-${var.environment}"
  #   decision_log     = "decision-log-${var.environment}"
  # }
}

module "iam_irsa" {
  source = "./modules/iam_irsa"
  # cluster_oidc_provider_arn = module.eks_cluster.oidc_provider_arn
  # service_accounts = {
  #   mlflow      = { namespace = "mlflow",      bucket = module.s3.mlflow_bucket }
  #   decisioner  = { namespace = "real-time-ml", bucket = module.s3.decision_log_bucket }
  # }
}

# Optional — only used if MLflow runs outside the cluster on EC2.
# Default: MLflow runs in-cluster (per ADR 005 custom Deployment).
# module "mlflow_server" {
#   source = "./modules/mlflow_server"
# }
