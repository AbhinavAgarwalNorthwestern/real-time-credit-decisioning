# Realtime Credit-Decisioning Platform — AWS infrastructure root module.
#
# Provisions everything OUTSIDE the K8s cluster:
#   - VPC + public/private subnets across 2 AZs
#   - EKS cluster (managed control plane)
#   - ECR repositories per containerized service
#   - S3 buckets (MLflow artifacts + decision audit log + SHAP results)
#   - IAM roles and IRSA service-account mappings
#
# K8s workloads (Deployments, Services, ConfigMaps) live in
# `deployments/overlays/aws-eks/` and apply via `kubectl apply -k`.

terraform {
  backend "s3" {
    bucket = "rtcd-terraform-state"
    key    = "infra/terraform.tfstate"
    region = "ap-south-1"
  }
}

module "vpc" {
  source       = "./modules/vpc"
  cluster_name = var.cluster_name
  aws_region   = var.aws_region
  tags         = var.tags
}

module "eks_cluster" {
  source          = "./modules/eks_cluster"
  cluster_name    = var.cluster_name
  cluster_version = var.cluster_version
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnet_ids
  environment     = var.environment
  tags            = var.tags
}

module "ecr" {
  source = "./modules/ecr"
  services = [
    "decisioner",
    "transactions",
    "drift-monitor",
    "outcome-collector",
    "shap-consumer",
    "retraining-flow",
  ]
  tags = var.tags
}

module "s3" {
  source      = "./modules/s3"
  environment = var.environment
  tags        = var.tags
}

module "iam_irsa" {
  source                   = "./modules/iam_irsa"
  cluster_oidc_provider_arn = module.eks_cluster.oidc_provider_arn
  cluster_oidc_issuer       = module.eks_cluster.oidc_issuer
  mlflow_bucket             = module.s3.mlflow_bucket
  decision_log_bucket       = module.s3.decision_log_bucket
  shap_results_bucket       = module.s3.shap_results_bucket
  tags                      = var.tags
}
