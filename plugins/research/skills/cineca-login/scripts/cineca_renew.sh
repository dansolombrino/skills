#!/usr/bin/env bash
set -euo pipefail

# CINECA Leonardo SSH certificate renewal via OIDC password grant.
#
# Credential backends (tried in order):
#   1. systemd credentials — $CREDENTIALS_DIRECTORY/{password,totp} populated by
#      LoadCredentialEncrypted= in the systemd service (TPM2-bound, zero plaintext on disk).
#   2. systemd-creds decrypt — same .cred files decrypted on the fly for interactive use.
# See references/setup.md for how to provision the credentials.

CRED_DIR="${CINECA_CRED_DIR:-$HOME/.config/cineca-hpc}"
CERT_FILE="${CINECA_CERT_FILE:-$HOME/.ssh/id_leonardo-cert.pub}"
KEY_FILE="${CINECA_KEY_FILE:-$HOME/.ssh/id_leonardo}"
PRINCIPAL="${CINECA_PRINCIPAL:-dsolombr}"
TOKEN_URL="https://sso.hpc.cineca.it/realms/CINECA-HPC/protocol/openid-connect/token"
CLIENT_ID="step-ca"
CLIENT_SECRET="hyIJK4PiYERYVzRa5RzVW7kPupC7GJ5F"
PROVISIONER="cineca-hpc"
RIG_HOST="${CINECA_RIG_HOST:-rig-4090}"
RENEW_MARGIN="${CINECA_RENEW_MARGIN:-60m}"

usage() {
  cat <<'USAGE'
Usage: cineca_renew.sh [--force] [--check-only] [--no-distribute] [--quiet]

  --force          Renew even if the current certificate is still valid.
  --check-only     Print certificate status and exit (no renewal).
  --no-distribute  Skip rsyncing the certificate to the rig hub.
  --quiet          Suppress informational output (errors still print).
USAGE
  exit 0
}

FORCE=false CHECK_ONLY=false DISTRIBUTE=true QUIET=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)         FORCE=true; shift ;;
    --check-only)    CHECK_ONLY=true; shift ;;
    --no-distribute) DISTRIBUTE=false; shift ;;
    --quiet)         QUIET=true; shift ;;
    -h|--help)       usage ;;
    *) echo "unknown flag: $1" >&2; exit 1 ;;
  esac
done

log() { $QUIET || echo "$@"; }

# --- status check -----------------------------------------------------------

cert_status() {
  if [[ ! -f "$CERT_FILE" ]]; then
    echo "missing"; return
  fi
  local valid
  valid=$(ssh-keygen -L -f "$CERT_FILE" 2>/dev/null | sed -n 's/^ *Valid: //p')
  if [[ -z "$valid" ]]; then
    echo "unreadable"; return
  fi
  echo "$valid"
}

if $CHECK_ONLY; then
  echo "cert: $CERT_FILE"
  echo "status: $(cert_status)"
  if [[ -f "$CERT_FILE" ]] && step ssh needs-renewal "$CERT_FILE" --expires-in="$RENEW_MARGIN" 2>/dev/null; then
    echo "needs-renewal: yes (within $RENEW_MARGIN)"
  else
    echo "needs-renewal: no"
  fi
  exit 0
fi

# --- freshness gate ----------------------------------------------------------

if ! $FORCE; then
  if [[ -f "$CERT_FILE" ]] && ! step ssh needs-renewal "$CERT_FILE" --expires-in="$RENEW_MARGIN" 2>/dev/null; then
    log "cert valid for >$RENEW_MARGIN, skipping (use --force to override)"
    exit 0
  fi
fi

# --- credentials -------------------------------------------------------------

load_credentials() {
  # Backend 1: systemd credentials directory (set by LoadCredentialEncrypted=)
  if [[ -n "${CREDENTIALS_DIRECTORY:-}" ]]; then
    local pw_file="$CREDENTIALS_DIRECTORY/password"
    local totp_file="$CREDENTIALS_DIRECTORY/totp"
    if [[ -r "$pw_file" && -r "$totp_file" ]]; then
      CINECA_PASSWORD=$(<"$pw_file")
      CINECA_TOTP_SECRET=$(<"$totp_file")
      log "credentials: systemd (TPM2-bound)"
      return 0
    fi
  fi

  # Backend 2: decrypt .cred files directly via systemd-creds (interactive use)
  local pw_cred="$CRED_DIR/password.cred"
  local totp_cred="$CRED_DIR/totp.cred"
  if [[ -f "$pw_cred" && -f "$totp_cred" ]]; then
    CINECA_PASSWORD=$(systemd-creds decrypt --with-key=tpm2 --name=password "$pw_cred" -) || {
      echo "error: failed to decrypt $pw_cred — is the user in the tss group?" >&2
      return 1
    }
    CINECA_TOTP_SECRET=$(systemd-creds decrypt --with-key=tpm2 --name=totp "$totp_cred" -) || {
      echo "error: failed to decrypt $totp_cred" >&2
      return 1
    }
    log "credentials: systemd-creds decrypt (TPM2)"
    return 0
  fi

  echo "error: no credential backend available" >&2
  echo "provision TPM2-bound credentials — see cineca-login references/setup.md" >&2
  return 1
}

load_credentials

if [[ -z "${CINECA_PASSWORD:-}" ]]; then
  echo "error: CINECA_PASSWORD not set by any credential backend" >&2; exit 1
fi
if [[ -z "${CINECA_TOTP_SECRET:-}" ]]; then
  echo "error: CINECA_TOTP_SECRET not set by any credential backend" >&2; exit 1
fi

# --- TOTP --------------------------------------------------------------------

OTP=$(oathtool --totp -b "$CINECA_TOTP_SECRET")

# --- OIDC token exchange -----------------------------------------------------

log "requesting OIDC token..."
RESPONSE=$(curl -s -w '\n%{http_code}' -X POST "$TOKEN_URL" \
  --data-urlencode "grant_type=password" \
  --data-urlencode "client_id=$CLIENT_ID" \
  --data-urlencode "client_secret=$CLIENT_SECRET" \
  --data-urlencode "username=$PRINCIPAL" \
  --data-urlencode "password=$CINECA_PASSWORD" \
  --data-urlencode "totp=$OTP" \
  --data-urlencode "scope=openid")

HTTP_BODY=$(echo "$RESPONSE" | sed '$d')
HTTP_CODE=$(echo "$RESPONSE" | tail -1)

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "error: OIDC token request failed (HTTP $HTTP_CODE)" >&2
  echo "$HTTP_BODY" | jq . 2>/dev/null || echo "$HTTP_BODY" >&2
  exit 1
fi

ID_TOKEN=$(printf '%s' "$HTTP_BODY" | jq -r .id_token)
if [[ -z "$ID_TOKEN" || "$ID_TOKEN" == "null" ]]; then
  echo "error: response has no id_token" >&2
  printf '%s' "$HTTP_BODY" | jq . >&2
  exit 1
fi

# --- issue certificate -------------------------------------------------------

log "issuing SSH certificate..."
step ssh certificate "$PRINCIPAL" "$KEY_FILE" \
  --provisioner "$PROVISIONER" \
  --token "$ID_TOKEN" \
  --no-password --insecure -f

log "certificate issued: $(ssh-keygen -L -f "$CERT_FILE" | sed -n 's/^ *Valid: //p')"

# --- distribute to rig hub ---------------------------------------------------

if $DISTRIBUTE && [[ -n "$RIG_HOST" ]]; then
  if [[ "$(hostname)" == "$RIG_HOST" ]]; then
    log "running on $RIG_HOST, no distribution needed"
  else
    log "distributing to $RIG_HOST..."
    rsync -az "$KEY_FILE" "${KEY_FILE}.pub" "$CERT_FILE" "$RIG_HOST:~/.ssh/"
    log "distributed to $RIG_HOST"
  fi
fi

# --- verify ------------------------------------------------------------------

if ssh -o BatchMode=yes -o ConnectTimeout=20 leonardo true 2>/dev/null; then
  log "verified: leonardo reachable"
else
  echo "warning: leonardo SSH probe failed (cert issued but login node may be busy)" >&2
fi
