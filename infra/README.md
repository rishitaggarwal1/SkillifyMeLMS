# infra/

- `local/` — configuration mounted into the docker-compose stack (Postgres init scripts, Keycloak dev
  realm).
- Terraform for AWS (VPC, ECS/EKS, Aurora, ElastiCache, MSK, S3, CloudFront + WAF) will be added in a
  later phase.
