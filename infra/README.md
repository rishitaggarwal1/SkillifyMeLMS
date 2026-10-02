# infra/

- `keycloak/` — the realm template (`realm.template.json`, plus `realm.dev-clients.json`, merged
  only with `KEYCLOAK_DEV_CLIENTS=true`) and `realm.py`, which renders it from `.env` (the
  `keycloak-realm` job) and syncs an existing realm (`keycloak-sync`). `scripts/` holds the
  optional Google identity-provider setup.
- `caddy/Caddyfile` — the edge proxy for a dev/demo server (`docker-compose.prod.yml`); every
  hostname comes from `.env`.
- `dev-vm/README.md` — runbook for that single-VM dev/demo server. Not yet executed.
- Terraform for AWS (VPC, ECS/EKS, Aurora, ElastiCache, MSK, S3, CloudFront + WAF) will be added in a
  later phase.
