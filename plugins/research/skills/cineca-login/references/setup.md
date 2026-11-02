# cineca-login setup

One-time steps to enable automatic Leonardo SSH certificate renewal.

## Prerequisites

| tool | install | purpose |
|---|---|---|
| `step` ≥ 0.26 | [smallstep.com/docs/step-cli](https://smallstep.com/docs/step-cli/installation/) | SSH certificate issuance |
| `oathtool` | `sudo apt install oathtool` (Debian/Ubuntu) | TOTP code generation |
| `jq` | `sudo apt install jq` | JSON parsing |
| `rsync` | usually pre-installed | certificate distribution to rig hub |

Bootstrap the CA (once):

```bash
step ca bootstrap --ca-url=https://sshproxy.hpc.cineca.it --fingerprint <fingerprint>
```

## Credentials (systemd-creds + TPM2)

Credentials are encrypted to the machine's TPM2 chip via `systemd-creds encrypt`. The ciphertext
is useless on any other machine, and plaintext never exists on persistent storage.

The script uses these credentials in two modes:
- **Under systemd** (the timer): `LoadCredentialEncrypted=` decrypts them into an ephemeral
  per-service tmpfs — zero sudo, zero prompts.
- **Interactive** (manual runs): `sudo systemd-creds decrypt` decrypts them on the fly.

**Provision the secrets** (once, interactively):

```bash
mkdir -p ~/.config/cineca-hpc
systemd-ask-password -n "CINECA password:" | sudo systemd-creds encrypt --with-key=tpm2 --name=password - ~/.config/cineca-hpc/password.cred
systemd-ask-password -n "CINECA TOTP secret:" | sudo systemd-creds encrypt --with-key=tpm2 --name=totp - ~/.config/cineca-hpc/totp.cred
```

Each command prompts for the value with no echo. Nothing touches shell history or disk in
plaintext.

The `.cred` files can safely live in backups — they are inert without this machine's TPM.

**The TOTP secret** is the base32 string from when 2FA was enrolled (the same string your
authenticator app uses). If you no longer have it, re-enroll 2FA at
`https://sso.hpc.cineca.it` to obtain a new one.

**To update** a credential, re-run the corresponding `systemd-creds encrypt` command — it
overwrites the `.cred` file.

## Systemd service and timer

Create `~/.config/systemd/user/cineca-cert-renew.service`:

```ini
[Unit]
Description=Renew CINECA Leonardo SSH certificate

[Service]
Type=oneshot
ExecStart=/usr/bin/bash %h/.local/share/skills/research/cineca-login/scripts/cineca_renew.sh --quiet
Environment=PATH=/usr/local/bin:/usr/bin:/bin
LoadCredentialEncrypted=password:%h/.config/cineca-hpc/password.cred
LoadCredentialEncrypted=totp:%h/.config/cineca-hpc/totp.cred
```

When systemd starts this service it decrypts the `.cred` files via TPM2, places the plaintext in
`$CREDENTIALS_DIRECTORY/{password,totp}` (a per-service tmpfs), and the script reads them from
there. The plaintext is never on persistent storage.

Create `~/.config/systemd/user/cineca-cert-renew.timer`:

```ini
[Unit]
Description=Renew CINECA Leonardo SSH certificate every 10 hours

[Timer]
OnBootSec=5min
OnUnitActiveSec=10h
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:

```bash
systemctl --user daemon-reload
systemctl --user enable --now cineca-cert-renew.timer
loginctl enable-linger $USER   # keeps timers running after logout
```

The timer fires every 10 hours. The script's freshness gate means it only actually renews when
the certificate is within 60 minutes of expiry, so stacking timers or manual runs is safe.

## Test the renewal

```bash
# Check current status
bash /path/to/cineca-login/scripts/cineca_renew.sh --check-only

# Force a renewal (interactive — uses sudo systemd-creds decrypt)
bash /path/to/cineca-login/scripts/cineca_renew.sh --force

# Test the systemd service (uses LoadCredentialEncrypted — no sudo)
systemctl --user start cineca-cert-renew.service
journalctl --user -u cineca-cert-renew.service --no-pager -n 20
```

## Integration with sweep-supervisor

The supervisor already emits `cert-expiring` and `cert-expired` events. To have it trigger
automatic renewal, set in the wave config or `~/.config/rigsync/machines.toml`:

```toml
[machines.leonardo]
cert_renew_command = "bash /path/to/cineca-login/scripts/cineca_renew.sh --quiet"
```

When sweep-supervisor detects the certificate is within the warning window, it runs this command
before notifying the user. If the command succeeds the notification says the cert was auto-renewed;
if it fails the notification says manual intervention is needed, same as before.

## Customisation

Environment variables override defaults (set in the systemd unit or shell):

| variable | default | meaning |
|---|---|---|
| `CINECA_CRED_DIR` | `~/.config/cineca-hpc` | directory containing `.cred` files |
| `CINECA_CERT_FILE` | `~/.ssh/id_leonardo-cert.pub` | certificate output path |
| `CINECA_KEY_FILE` | `~/.ssh/id_leonardo` | SSH key path |
| `CINECA_PRINCIPAL` | `dsolombr` | CINECA username |
| `CINECA_RIG_HOST` | `rig-4090` | rig hub to distribute the cert to (empty to skip) |
| `CINECA_RENEW_MARGIN` | `60m` | renew when cert expires within this window |

## Security notes

- Credentials are encrypted to the TPM2 chip. The ciphertext files are inert without this
  machine's TPM — they cannot be decrypted on another machine, from a disk image, or after a
  TPM clear. Plaintext exists only in an ephemeral per-service tmpfs (systemd) or briefly in
  shell variables (interactive decrypt).
- The `CLIENT_SECRET` in the script is not a user secret — it is the OIDC client secret for the
  `step-ca` public client, visible to anyone who runs `step ca provisioner list`. It identifies
  the application, not the user.
- The script never logs credentials or tokens. On failure it prints the HTTP status and the
  error body (which contains no secrets).
