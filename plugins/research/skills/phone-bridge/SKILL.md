---
name: phone-bridge
description: Run the hub's Telegram bot that reaches the user's phone - it pushes sweep-supervisor notices and renews a cluster's short-lived OIDC SSH certificate (CINECA Leonardo via smallstep) by sending a login link before expiry and taking back the pasted http://127.0.0.1 redirect, so the 2FA login is the user's only step. Use when the user asks to set up, install, check, or troubleshoot the phone bot or its notices, to renew the Leonardo (or another step-ca) certificate from the phone, to see when a certificate expires, or to wire notifications for sweep-supervisor. Do not use to store passwords or one-time-code secrets, or to automate the login itself.
---

# phone-bridge

A stdlib Telegram bot on the hub (`scripts/bridge.py`, in this skill's installed directory — the
folder holding this SKILL.md, never the research project). It does two things:

- **Notices.** `bridge.py send "<text>"` pushes one line to the user's chat. It is the
  `sweep-supervisor` `notify_command`, so finished waves, failures, rig faults, questions and
  follow-up reports reach the phone with no chat open.
- **Certificate renewal.** A cluster whose SSH access is a short-lived certificate signed after a
  browser login with a password and a one-time code (CINECA Leonardo: smallstep CA
  `sshproxy.hpc.cineca.it`, provisioner `cineca-hpc`) is renewed with the user doing only the
  login, from the phone.

## The renewal, end to end

1. An hour before the certificate expires (and again when it has), or on `/renew_<rig>` (or the
   rig's `command`), the bot sends a
   login link: an OIDC authorization request with PKCE, built from the CA's published provisioner.
   The verifier stays on the hub (`state_dir`, owner-only).
2. The user logs in on the identity provider's page with password and one-time code.
3. The identity provider accepts exactly one redirect for this client, `http://127.0.0.1:<port>`
   (the provisioner's `listenAddress`, 10000 for CINECA), so the phone's browser fails to open it.
   The user copies that address from the address bar and pastes it to the bot. That is the whole
   manual step; no router port or tunnel is involved, and the code in it is useless without the
   hub's verifier.
4. The bot redeems the code, has `step ssh certificate --sign --token` sign the **existing** public
   key for the longest validity the CA allows (`maxUserSSHCertDuration`, 48 h on CINECA rather than
   the 12 h default), checks the new expiry, and replies with it. `sweep-supervisor` picks the rig
   back up on its next cycle by itself.

A code expires within about a minute of the redirect: paste promptly, or ask for a new link.

## Limits

- The login is always the user's: the bridge does not store the password, the one-time-code
  secret, or recovery codes, and does not script the login page (that would remove the second
  factor, which the cluster does not permit).
- It answers only `phone.chat_id` and never prints or logs the bot token, a code, a verifier, or
  a token.
- It signs the key the SSH config already uses rather than generating a new pair.

## Setup (once per hub)

Read [references/setup.md](references/setup.md). In short: the user creates a bot with
@BotFather and writes its token to an owner-only file; `[phone]` and each rig's
`[machines.<rig>.ssh_login]` go into the machine registry; `chat-id` finds the chat id after the
user messages the bot; `check` must pass; the service is installed from
`assets/phone-bridge.service`; the supervisor's `notify_command` points at `send`. The user
creates the bot and its token (a new account); the remaining steps are scripted.

```bash
BRIDGE=/absolute/path/to/phone-bridge/scripts/bridge.py
python3 "$BRIDGE" check        # token file, chat id, every ssh_login
python3 "$BRIDGE" chat-id      # chats that messaged the bot
python3 "$BRIDGE" status       # when each certificate expires
python3 "$BRIDGE" send "text"  # one notice
python3 "$BRIDGE" serve        # the bot (run by the user service)
```

In the chat with the bot: each rig's renewal command (`/renew_<rig>`, or its `command`), `/status`,
or a pasted redirect address; `serve` publishes them as the chat's command menu. Running from a
versioned plugin install, `serve` moves itself onto a newer installed version once its `check`
passes.
