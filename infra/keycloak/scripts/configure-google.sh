#!/usr/bin/env bash
# Adds (or updates) Google as an identity provider in the realm when GOOGLE_CLIENT_ID and
# GOOGLE_CLIENT_SECRET are set. Without them it does nothing, so no broken Google button appears.
# First login through Google uses the "first broker login - auto link" flow from the realm import:
# a Google account whose (verified) email matches an invited/imported user is linked to it.
set -euo pipefail

if [[ -z "${GOOGLE_CLIENT_ID:-}" || -z "${GOOGLE_CLIENT_SECRET:-}" ]]; then
  echo "Google IdP not configured (GOOGLE_CLIENT_ID/SECRET empty); skipping."
  exit 0
fi

kcadm=/opt/keycloak/bin/kcadm.sh
"$kcadm" config credentials --server "$KEYCLOAK_URL" --realm master \
  --user "$KEYCLOAK_ADMIN" --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null

settings=(
  -s alias=google -s providerId=google -s enabled=true -s trustEmail=true
  -s "firstBrokerLoginFlowAlias=first broker login - auto link"
  -s "config.clientId=$GOOGLE_CLIENT_ID" -s "config.clientSecret=$GOOGLE_CLIENT_SECRET"
  -s config.syncMode=IMPORT -s "config.defaultScope=openid email profile"
)
if "$kcadm" get "identity-provider/instances/google" -r "$KEYCLOAK_REALM" >/dev/null 2>&1; then
  "$kcadm" update identity-provider/instances/google -r "$KEYCLOAK_REALM" "${settings[@]}"
  echo "Updated Google identity provider."
else
  "$kcadm" create identity-provider/instances -r "$KEYCLOAK_REALM" "${settings[@]}"
  echo "Created Google identity provider."
fi
