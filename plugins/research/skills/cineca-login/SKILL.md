---
name: cineca-login
description: Renew the CINECA Leonardo SSH certificate non-interactively via an OIDC password grant with programmatic TOTP, distribute it to the rig hub, and optionally run on a systemd timer so the 12-hour certificate never expires unnoticed. Use when the certificate is about to expire, when sweep-supervisor fires a cert-expiring event, or to set up unattended renewal.
---

# cineca-login

Leonardo SSH certificates are issued by CINECA's smallstep CA (`sshproxy.hpc.cineca.it`) through
the `cineca-hpc` OIDC provisioner. They are valid for **12 hours**. This skill renews them
without a browser by exchanging the user's CINECA password and a programmatically generated TOTP
code for an OIDC id\_token, then passing it to `step ssh certificate --token`.

Setup (one-time): [references/setup.md](references/setup.md).

## Credential backends

Credentials (CINECA password + TOTP secret) never exist in plaintext on disk. The script tries
two backends in order:

1. **systemd service** (headless timer) — the systemd unit declares `LoadCredentialEncrypted=`,
   which decrypts the TPM2-bound `.cred` files into an ephemeral per-service tmpfs at runtime.
2. **systemd-creds decrypt** (interactive use) — the same `.cred` files are decrypted on the fly
   via `sudo systemd-creds decrypt`. The ciphertext is inert without this machine's TPM.

## Renewal flow

1. **Freshness gate** — `step ssh needs-renewal` skips renewal if the certificate is valid for
   longer than the configured margin (default 60 minutes).
2. **Credential load** — reads password and TOTP secret from the first available backend.
3. **TOTP generation** — `oathtool --totp -b` produces a one-time code from the secret.
4. **OIDC token exchange** — a `password` grant against the CINECA Keycloak realm returns an
   `id_token`.
5. **Certificate issuance** — `step ssh certificate --token` issues a new 12-hour certificate
   without opening a browser.
6. **Distribution** — the certificate and key are rsynced to the rig hub so the dispatch chain
   can reach Leonardo.
7. **Verification** — an SSH probe confirms Leonardo is reachable.

## Commands

All paths below refer to this skill's installed directory.

```bash
CINECA_SCRIPT=/path/to/cineca-login/scripts/cineca_renew.sh

# Check certificate status (no renewal)
bash "$CINECA_SCRIPT" --check-only

# Normal renewal (skips if cert still fresh)
bash "$CINECA_SCRIPT"

# Force renewal regardless of freshness
bash "$CINECA_SCRIPT" --force

# Renew without distributing to rig hub
bash "$CINECA_SCRIPT" --no-distribute

# Quiet mode (for timers — errors still print)
bash "$CINECA_SCRIPT" --quiet
```

## When to use

- **Before a wave touches Leonardo** — `sweep-dispatch` checks the certificate as a precondition.
  If it is expired or missing, run this skill's script before proceeding.
- **On `cert-expiring` from `sweep-supervisor`** — the supervisor detects certificates within the
  warning window. With `cert_renew_command` configured, it triggers renewal automatically; without
  it, it notifies the user to run this skill manually.
- **Unattended via systemd timer** — the recommended setup. A timer every 10 hours keeps the
  certificate fresh without human intervention. See [references/setup.md](references/setup.md).

## Integration with other skills

- **`sweep-dispatch`**: the precondition block in `references/cineca-slurm.md` that checks
  certificate validity can now call this script as a self-healing step before falling back to the
  manual-renewal notice.
- **`sweep-supervisor`**: set `cert_renew_command` in the machine registry to have the supervisor
  run renewal on the `cert-expiring` event. A successful renewal is logged; a failure still
  triggers the existing notification.
- **`rig-sync`**: certificate distribution uses the same rsync path as the manual flow.

## Prerequisites

| tool | why |
|---|---|
| `step` ≥ 0.26 | certificate issuance |
| `oathtool` | TOTP generation (package: `oath-toolkit`) |
| `jq` | JSON parsing |
| `rsync` | certificate distribution |

Credentials: TPM2-bound `.cred` files in `~/.config/cineca-hpc/`, provisioned once via
`systemd-creds encrypt`.
Details in [references/setup.md](references/setup.md).
