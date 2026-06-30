variable "services" {
  type = list(string)
  default = [
    "decisioner",
    "transactions",
    "drift-monitor",
    "outcome-collector",
    "shap-consumer",
    "retraining-flow",
  ]
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_ecr_repository" "service" {
  for_each = toset(var.services)

  name                 = "realtime-credit/${each.value}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(var.tags, {
    Module  = "ecr"
    Service = each.value
  })
}

resource "aws_ecr_lifecycle_policy" "cleanup" {
  for_each   = toset(var.services)
  repository = aws_ecr_repository.service[each.value].name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 30 tagged images"
        selection = {
          tagStatus   = "tagged"
          tagPrefixList = ["v"]
          countType   = "imageCountMoreThan"
          countNumber = 30
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Remove untagged after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      }
    ]
  })
}

output "repository_urls" {
  value = { for k, v in aws_ecr_repository.service : k => v.repository_url }
}
