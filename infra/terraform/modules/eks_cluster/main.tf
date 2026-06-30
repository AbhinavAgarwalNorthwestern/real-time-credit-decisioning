variable "cluster_name" {
  type = string
}

variable "cluster_version" {
  type    = string
  default = "1.30"
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "tags" {
  type    = map(string)
  default = {}
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.cluster_name
  cluster_version = var.cluster_version

  vpc_id     = var.vpc_id
  subnet_ids = var.subnet_ids

  cluster_endpoint_public_access = true

  enable_irsa = true

  eks_managed_node_groups = {
    default = {
      # 5 m6i.large for v1.1.0 cluster validation: headroom for all 11
      # services + Strimzi Kafka + RisingWave + MLflow + Postgres.
      # 5 nodes = 40 GB RAM total.
      #
      # disk_size = 100 GiB (default would be 20 GiB). 100 GiB is sized
      # for: cached container images for all 11 services (~20 GB) +
      # workload ephemeral storage during training Jobs (5 GB scratch +
      # parquet outputs + model artifacts) + headroom above the kubelet
      # eviction threshold. 20 GiB nodes evict training pods during
      # image pull; documented in `docs/SESSION_ERRORS_2026-06-30.md`.
      instance_types = ["m6i.large"]
      min_size       = 3
      max_size       = 8
      desired_size   = 5

      # block_device_mappings is required for the terraform-aws-modules/eks
      # v20 launch template to honor a custom root volume size. The
      # top-level `disk_size` attribute is IGNORED when a launch template
      # is generated, which is what this module does by default.
      block_device_mappings = {
        xvda = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size           = 100
            volume_type           = "gp3"
            encrypted             = true
            delete_on_termination = true
          }
        }
      }

      # Allow rolling update with minimal disruption to running workloads.
      update_config = {
        max_unavailable_percentage = 33
      }

      labels = {
        Environment = var.environment
        Project     = "realtime-credit-decisioning"
      }
    }

    gpu = {
      instance_types = ["g5.xlarge"]
      min_size       = 0
      max_size       = 2
      desired_size   = 0
      ami_type       = "AL2_x86_64_GPU"

      labels = {
        "nvidia.com/gpu" = "true"
        workload         = "training"
      }

      taints = [{
        key    = "nvidia.com/gpu"
        value  = "true"
        effect = "NO_SCHEDULE"
      }]
    }
  }

  cluster_addons = {
    vpc-cni = {
      most_recent = true
    }
    kube-proxy = {
      most_recent = true
    }
    coredns = {
      most_recent = true
    }
    aws-ebs-csi-driver = {
      most_recent              = true
      service_account_role_arn = null
    }
  }

  tags = merge(var.tags, {
    Module = "eks_cluster"
  })
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "cluster_name" {
  value = module.eks.cluster_name
}

output "oidc_provider_arn" {
  value = module.eks.oidc_provider_arn
}

output "cluster_certificate_authority_data" {
  value = module.eks.cluster_certificate_authority_data
}

output "oidc_issuer" {
  value = module.eks.cluster_oidc_issuer_url
}
