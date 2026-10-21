# phone-bridge setup

## 1. The bot (the user's step)

In Telegram, talk to @BotFather: `/newbot`, pick a name. Write the token it prints to a file only
the hub user can read, without pasting it into any chat with an agent:

```bash
mkdir -p ~/.config/phone-bridge && umask 077 && read -rs TOKEN && printf '%s\n' "$TOKEN" > ~/.config/phone-bridge/telegram.token && unset TOKEN
```

Then send the new bot any message from the phone.

## 2. Registry (`~/.config/rigsync/machines.toml`)

```toml
[phone]
token_file = "~/.config/phone-bridge/telegram.token"
chat_id = 123456789                      # from `bridge.py chat-id`
# state_dir = "~/.local/state/phone-bridge"   # default; holds pending logins, owner-only

[machines.leonardo]
ssh_cert = "~/.ssh/id_leonardo-cert.pub"  # also read by sweep-supervisor's certificate watch

[machines.leonardo.ssh_login]
identity = "<login e-mail>"               # the certificate's Key ID
provisioner = "cineca-hpc"                # `step ca provisioner list`
public_key = "~/.ssh/id_leonardo.pub"     # signed in place; its -cert.pub is ssh_cert
command = "renew_cineca"                  # bot command for this rig; default renew_<rig>
```

`step` must already be bootstrapped against the CA (`step ca bootstrap --ca-url ...
--fingerprint ...`, the cluster's documented one-time setup), so `step ca provisioner list` works.

## 3. Service

```bash
mkdir -p ~/.config/systemd/user && cp /absolute/path/to/phone-bridge/assets/phone-bridge.service ~/.config/systemd/user/
sed -i "s|__BRIDGE_PY__|/absolute/path/to/phone-bridge/scripts/bridge.py|" ~/.config/systemd/user/phone-bridge.service
systemctl --user daemon-reload && systemctl --user enable --now phone-bridge.service
```

## 4. Supervisor notices

```toml
[supervisor]
notify_command = ["python3", "/absolute/path/to/phone-bridge/scripts/bridge.py", "send", "{text}"]
```

The supervisor's own certificate notices then arrive too; the bridge's carry the login link.
