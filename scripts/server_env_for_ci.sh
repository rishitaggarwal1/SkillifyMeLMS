#!/usr/bin/env bash
# Writes a server-style env file for CI config checks: .env.example, then .env.dev-server.example
# with every placeholder replaced by an obviously fake value on example.test. Nothing here is a
# real secret; the file never leaves the runner.
#
#   scripts/server_env_for_ci.sh > .env.server
set -euo pipefail
cd "$(dirname "$0")/.."

server=$(sed \
  -e 's/<DOMAIN>/example.test/g' \
  -e 's/<SECRET>/ci-placeholder-not-a-secret-0123456789/g' \
  -e 's/<OPS_EMAIL>/ops@example.test/g' \
  -e 's#<ADMIN_CIDR>#198.51.100.0/24#g' \
  -e 's#<REGISTRY>/skillifyme-api:<GIT_SHA>#skillifyme-api:ci#g' \
  -e 's#<REGISTRY>/skillifyme-web:<GIT_SHA>#skillifyme-web:ci#g' \
  -e 's/<MAILPIT_USER>/ops/g' \
  -e 's/<BCRYPT_HASH>/ci-hash-replaced-at-validate-time/g' \
  .env.dev-server.example)

# A placeholder this script doesn't know would reach compose as a literal: fail instead.
if leftover=$(grep -v '^#' <<<"$server" | grep -oE '<[A-Z_]+>'); then
  echo "unhandled placeholders in .env.dev-server.example: $leftover" >&2
  exit 1
fi

cat .env.example
echo
echo "# ---- .env.dev-server.example (CI placeholders)"
echo "$server"
