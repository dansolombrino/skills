# phone-bridge setup

## 1. The bot (the user's step)

In Telegram, talk to @BotFather: `/newbot`, pick a name. Write the token it prints to a file only
the hub user can read, without pasting it into any chat with an agent:

```bash
mkdir -p ~/.config/phone-bridge && umask 077 && read -rs TOKEN && printf '%s\n' "$TOKEN" > ~/.config/phone-bridge/telegram.token && unset TOKEN
```

Then make the chat the bot lives in, so each wave gets its own thread:

1. New group with just you and the bot; in its settings turn **Topics** on (this makes it a
   supergroup with a new id, so do it before the next steps). Keep it private: everyone in it can
   talk to the agents.
2. Make the bot an admin with **Manage topics** and **Pin messages**. An admin bot sees every message,
   which it needs to hear you in a topic.
3. Write anything in the group, then run `chat-id`: the row marked `supergroup with topics` (a
   negative id) is `phone.chat_id`.

Finished waves' topics are closed (a lock, history kept, still writable by you and the bot), so the
live ones stay on top.

**A group per project (optional).** Bots cannot create groups, so for each project that should have
its own, repeat steps 1–3 with a group named after it, then map the project (its root's folder
name, as the topics show it) to that group's id under `[phone.projects]`. Its waves' topics are
then named by the wave alone; a project without a group keeps sharing the main chat, where each
project's topics share an icon color. Mapping a project while a wave runs moves that wave to a new
topic in the group and closes the old one. Certificate notices always go to the main chat. A Telegram
chat folder holding the groups keeps them together on the phone.

A one-to-one chat with the bot also works, without threads: every wave shares it, the wave name
prefixes each notice, a reply reaches the wave of the message it replies to, and plain text goes to
the wave that wrote last.

## 2. Registry (`~/.config/rigsync/machines.toml`)

```toml
[phone]
token_file = "~/.config/phone-bridge/telegram.token"
chat_id = -1001234567890                 # from `bridge.py chat-id`: the group with topics
# state_dir = "~/.local/state/phone-bridge"   # default; pending logins, wave threads, owner-only

[phone.projects]                          # optional: projects with their own group
my-project = -1009876543210               # project root's folder name = its group's chat id

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

## 4. Supervisor notices and conversations

```toml
[supervisor]
notify_command = ["python3", "/absolute/path/to/phone-bridge/scripts/bridge.py", "send", "{text}", "--context", "{context}"]
```

`{context}` tells the bridge which wave and agent session a notice comes from, so it lands in that
wave's thread and a reply finds its agent. The supervisor's own certificate notices arrive too; the
bridge's carry the login link. For a reply to continue the agent's conversation instead of starting
a fresh agent, the supervisor's agent profile declares `resume_command` (its setup reference).

The bridge calls `sweep-supervisor`'s script beside this skill (`tell`, `table`, `waves`), so both
come from the same plugin install. In the state directory, `chat.json` maps every message the bot
sent to its wave and session; deleting it only forgets threads and routing. The bot obeys only
`phone.chat_id` and the groups under `[phone.projects]`.
