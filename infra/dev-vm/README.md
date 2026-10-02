# Dev/demo server runbook (single VM)

> **Status: NOT YET EXECUTED.** Nothing has been provisioned or deployed. This runbook was written
> and its configuration validated in CI (`docker compose config` with a server-style `.env`, and
> `caddy validate`), but no step below has been run against a real machine. Expect to correct it
> on the first run, and record what changed here.

**This is a dev/demo environment, not the production topology.** It is one VM running the whole
stack with Docker Compose behind Caddy, for demos and shared testing. It has no high availability,
no managed databases, no CDN or WAF, and its backups are only as good as the cron job below. The
production target (multi-AZ, managed Postgres/Redis/Kafka, S3 and CloudFront, autoscaling) is
described in [`docs/architecture.md`](../../docs/architecture.md#production-architecture-target)
and will be built with Terraform. Don't put real student data on this server.

## What runs

| Hostname (`.env`) | Service | Notes |
|---|---|---|
| `WEB_HOST`, e.g. `dev.<DOMAIN>` | web (Next.js standalone) | The API is reached only through the web app's `/backend` proxy |
| `AUTH_HOST`, e.g. `auth.dev.<DOMAIN>` | Keycloak (production mode) | `/admin` only from `ADMIN_ALLOW_CIDR`; brute-force protection on |
| `FILES_HOST`, e.g. `files.dev.<DOMAIN>` | MinIO | Presigned uploads and downloads |
| `MAIL_HOST`, e.g. `mail.dev.<DOMAIN>` | Mailpit | Catches all email; basic auth |

Caddy is the only container that publishes ports (80 and 443) and gets TLS certificates from
Let's Encrypt. Postgres, Redis, Redpanda, the API and the workers are reachable only inside the
Docker network. Files: [`docker-compose.yml`](../../docker-compose.yml) plus
[`docker-compose.prod.yml`](../../docker-compose.prod.yml), and
[`infra/caddy/Caddyfile`](../caddy/Caddyfile). Every setting comes from `.env`;
[`.env.dev-server.example`](../../.env.dev-server.example) lists the keys that differ from local.

All commands below run on the VM from the repository checkout, with:

```bash
export COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml   # both files, every command
```

## 1. Provision

1. **VM:** Ubuntu 24.04 LTS, at least 4 vCPU, 16 GB RAM and 100 GB SSD (Keycloak, Redpanda,
   Postgres and the app share it). Choose an Indian region if one is available, and record it.
   Any regulated data residency question goes to the data owner first.
2. **Access:** SSH keys only (`PasswordAuthentication no`), a named admin user, no root login.
3. **Firewall** (provider firewall and `ufw`): inbound 22 from the admin network only, plus 80 and
   443 from anywhere. Nothing else.
4. **Packages:** Docker Engine and the Compose plugin from Docker's apt repository (Compose 2.24 or
   newer: the override uses `!reset`), `git`, `make`, and `unattended-upgrades`.
5. **Disk:** keep Docker's data root on the SSD; leave 30% free for backups and image pulls.

## 2. DNS records

Create these records at the domain's DNS provider, pointing at the VM's public IPv4 address (add
AAAA records too if the VM has IPv6):

| Type | Name | Value |
|---|---|---|
| A | `dev.<DOMAIN>` | VM IP |
| A | `auth.dev.<DOMAIN>` | VM IP |
| A | `files.dev.<DOMAIN>` | VM IP |
| A | `mail.dev.<DOMAIN>` | VM IP |

Wait until each name resolves (`dig +short auth.dev.<DOMAIN>`) before the first start, or
Let's Encrypt validation fails and Caddy retries with backoff.

## 3. First deploy

1. **Code:** `git clone` the repository and check out the release tag being deployed.
2. **Images:** CI builds the `runtime` images on every push but doesn't push them to a registry
   yet. Until it does, build them on the VM from the same tag:

   ```bash
   docker build --target runtime -t skillifyme-api:<TAG> apps/api
   docker build --target runtime -t skillifyme-web:<TAG> apps/web
   ```

   and set `API_IMAGE=skillifyme-api:<TAG>` and `WEB_IMAGE=skillifyme-web:<TAG>` in `.env`.
3. **`.env`:** `cp .env.example .env`, append `.env.dev-server.example`, then replace every
   placeholder (instructions at the top of that file). Generate each `<SECRET>` fresh and store
   it in the team's secret store first; `.env` is a copy, not the source. Then
   `chmod 600 .env`. Check that no `<...>` placeholder is left:
   `grep -n '<[A-Z_]*>' .env` must print nothing.
4. **Validate before starting:**

   ```bash
   docker compose config -q
   docker compose run --rm --no-deps --entrypoint caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
   ```

5. **Start:** `docker compose up -d --wait`. Order: Postgres, then `migrate` (it creates the
   database roles, including Keycloak's own role and database, and applies migrations), the
   realm render, Keycloak (it imports the realm on first start), `keycloak-sync`, then the API,
   workers and web, then Caddy.
6. **Demo logins (optional):** `docker compose --profile tools run --rm seed-demo`. Passwords go
   only to `.secrets/demo-credentials.txt` (mode 0600). Share them through the secret store,
   never in chat or email.
7. **Check:**
   - `https://<WEB_HOST>` loads and sign-in goes through `https://<AUTH_HOST>`.
   - `https://<AUTH_HOST>/admin` returns 403 from outside `ADMIN_ALLOW_CIDR`.
   - `https://<MAIL_HOST>` asks for the basic-auth login.
   - An invitation email arrives in Mailpit, and its link points at `https://<WEB_HOST>`.
   - Only ports 80 and 443 answer from outside: `nmap -Pn <VM IP>` from another machine.

**Upgrades:** build or pull the new images, update `API_IMAGE`/`WEB_IMAGE`, take a backup
(below), then `docker compose up -d --wait`. `migrate` runs before the API starts. Realm setting
changes (redirect URIs, SMTP, brute force) are applied by `keycloak-sync` on every start.

## 4. Backups

What needs backing up: both Postgres databases (the platform's and Keycloak's), the MinIO bucket,
and `.env` (already in the secret store). Redis holds only buffered video heartbeats (minutes of
watch progress), so it is not backed up.

```bash
# Nightly, from cron, as the admin user. Keeps 14 days locally; copy off the VM (see below).
ts=$(date -u +%Y%m%dT%H%MZ)
mkdir -p ~/backups/$ts
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" > ~/backups/$ts/platform.dump
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -Fc keycloak > ~/backups/$ts/keycloak.dump
docker compose run --rm --no-deps -v ~/backups/$ts:/backup --entrypoint mc minio-init \
  mirror --quiet local/"$S3_BUCKET" /backup/files
find ~/backups -mindepth 1 -maxdepth 1 -mtime +14 -exec rm -rf {} +
```

- Copy each night's folder to storage outside the VM, in an account the VM can write to but not
  delete from, and encrypt it at rest.
- `POSTGRES_USER`, `POSTGRES_DB` and `S3_BUCKET` come from `.env` (`set -a; . ./.env; set +a`
  in the cron script).
- Test a restore (section 6) at least once before relying on the backups.

## 5. Rotate secrets

Generate each new value fresh, update the secret store first, then `.env`. Each change below is
followed by `docker compose up -d --wait`, which recreates only the containers whose settings
changed.

| Secret | How |
|---|---|
| `APP_DB_PASSWORD`, `RELAY_DB_PASSWORD`, `KEYCLOAK_DB_PASSWORD` | `migrate` runs `db_roles`, which sets the new role passwords. The services restart with them |
| `POSTGRES_PASSWORD` (owner) | `docker compose exec postgres psql -U "$POSTGRES_USER" -c "\password"` (it prompts), then update `.env` |
| `KC_WEB_CLIENT_SECRET`, `KC_ADMIN_CLIENT_SECRET` | `keycloak-sync` never changes client secrets. Regenerate the secret in the Keycloak admin console (Clients, Credentials), copy it into `.env`, then restart `api`, `worker` and `web` |
| `KEYCLOAK_ADMIN_PASSWORD` | Used only to create the first admin and by `keycloak-sync`. Change the admin's password in the admin console, then in `.env` |
| `SESSION_SECRET` | Signs everyone out of the web app |
| `REVALIDATE_SECRET` | Shared by the API, the workers and web; restart all three together |
| `MINIO_ROOT_PASSWORD` | `mc admin user` can't change the root user; update `.env` and restart `minio` and the API services |
| `MAILPIT_BASIC_AUTH_HASH` | New hash from `caddy hash-password` (write `$` as `$$`), then restart `caddy` |
| Demo passwords | `docker compose --profile tools run --rm seed-demo python -m app.cli.seed_demo --rotate-passwords --i-know-this-is-not-local`. The flag is required because the server isn't `ENVIRONMENT=local` |

If a secret may have leaked, treat it as an incident: report it to the security owner before or
while rotating, rather than only rotating it quietly.

## 6. Restore

1. Stop the app: `docker compose stop caddy web api worker beat outbox-relay enrollment-consumer keycloak`.
2. Restore the databases from a backup folder:

   ```bash
   docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < platform.dump
   docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d keycloak --clean --if-exists < keycloak.dump
   ```

3. Restore the files: `mc mirror` from the backup's `files/` folder back to `local/$S3_BUCKET`,
   with the same `minio-init` image as the backup.
4. Start everything again (`docker compose up -d --wait`) and repeat the checks from step 3.7.

## 7. Tear down

`docker compose down` stops the stack and keeps the data volumes. `docker compose down -v` also
**deletes every database, file and certificate**: take and copy a final backup first, then remove
the DNS records, then destroy the VM.
