variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "ap-south-1"
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "real-time-ml-prod"
}

variable "cluster_version" {
  description = "EKS Kubernetes version"
  type        = string
  default     = "1.30"
}

variable "environment" {
  description = "Deployment environment label (dev/staging/prod)"
  type        = string
  default     = "prod"
}

variable "tags" {
  description = "Tags applied to every taggable resource"
  type        = map(string)
  default = {
    Project   = "realtime-credit-decisioning"
    ManagedBy = "terraform"
  }
}
