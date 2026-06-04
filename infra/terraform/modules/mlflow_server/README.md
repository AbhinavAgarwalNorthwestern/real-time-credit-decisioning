# mlflow_server module — placeholder (OPTIONAL)

EC2-based MLflow tracking server, used **only if** MLflow runs outside
the K8s cluster. Default for this platform is **in-cluster** via the
custom Deployment per ADR 005.

This module exists for the AWS-style pattern from `project_01` where
MLflow lives on a small `t3.medium` EC2 with SQLite + hourly S3 snapshot.

## Day 8 implementation plan (only if used)

- `aws_instance` `t3.medium`, public subnet (or private with bastion)
- User data installs Python + mlflow + boto3
- Hourly cron to `aws s3 cp mlflow.db s3://.../mlflow.db.$(date)`
- Reverse proxy with TLS via Caddy
- Security group: 443 from the cluster CIDR only

## When to enable

Use this module **instead of** the in-cluster MLflow Deployment when:

- The cluster gets recycled frequently (in-cluster MLflow state would
  be lost without external Postgres)
- Compliance requires the tracking DB outside the application cluster
- A separate ML platform team owns MLflow lifecycle independently

Default for this project: do NOT enable this module — keep MLflow in
the cluster per ADR 005.
