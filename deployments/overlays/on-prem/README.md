# `overlays/on-prem/` — placeholder for future on-premise deployment

Skeleton only. Populate when there's a concrete on-prem target.

Expected patches:
- Images from an internal registry (Harbor / Nexus / JFrog)
- Storage class from an internal CSI driver
- Secrets from HashiCorp Vault via External Secrets Operator
- Object store: internal Ceph or NetApp with S3-compatible API (same
  `OBJECT_STORE_ENDPOINT` pattern as the local-kind overlay)
- Ingress: MetalLB or internal NGINX ingress controller
- No IRSA — use ServiceAccount-mounted tokens for in-cluster auth

The architectural pattern is identical to `aws-eks/` and `local-kind/`;
only the patch values change.
