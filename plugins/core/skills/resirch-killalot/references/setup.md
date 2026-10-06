# ReSirch Killalot — setup

One-time setup on the hub (the always-on machine that holds the projects). Every command is one
line.

## 1. Config

Create `~/.config/resirch-killalot/config.toml` (or point `KILLALOT_CONFIG` at another path):

```toml
[store]
root = "/abs/path/outside/every/project/resirch_killalot"   # store, backups, deployed releases

[projects]
roots = ["/abs/path/to/Projects", "/another/disk/Projects"]   # walked for .project.toml
max_depth = 5      # how deep under each root a project may sit
stale_days = 14    # no activity for this long, with items open, marks a project stale

[web]
bind = "127.0.0.1"                       # never a public address: tailscale serve is the only way in
port = 49147                             # fixed: see "Port" below
owner_login = "<your Tailscale login>"   # as shown by `tailscale status --json` → User → LoginName
public_base = "https://<hub>.<tailnet>.ts.net:49147"

[approvals]
ttl_hours = 24     # how long an approval stays valid
```

`killalot scan` fills the project list. `killalot projects` shows it.

## 2. Deploy the service (from the installed release)

The service runs from a copy of a **released** version, never from a repository working tree.
Run deploy with the installed plugin's copy of the script, i.e. the `scripts/killalot.py` of this
skill inside the host's plugin cache:

```bash
python3 <core plugin install path>/skills/resirch-killalot/scripts/killalot.py deploy
```

Deploy copies the release to `<store root>/app/<version>/`, points `app/current` at it, writes the
`~/.local/bin/killalot` wrapper, installs the `resirch-killalot.service` user unit, restarts it,
and waits for `/healthz`. The repository's release script re-runs it after every core release
once the unit exists. Keep the service running without a login session with
`loginctl enable-linger "$USER"`.

`killalot doctor` checks the config, the store, the wrapper, the service, and tailscale serve.

## 3. Reach it from the phone and the desk

Expose the localhost port on the tailnet only, over HTTPS:

```bash
tailscale serve --bg --https=49147 http://127.0.0.1:49147
```

Every device must be signed in to the tailnet as `owner_login`. The server answers only requests
that `tailscale serve` marks with that identity, **and** that carry a paired device cookie.

### Port

ReSirch Killalot always answers on **49147**: that is the one address the owner opens, and the
hub's router forwards it. What listens behind it depends on the access mode:

- **`tailscale`** (the default): the app listens on `127.0.0.1:49147` and `tailscale serve`
  publishes it as `https://<hub>.<tailnet>.ts.net:49147`, reachable only from the tailnet.
- **`public`**: a TLS proxy (Caddy) owns `0.0.0.0:49147` and serves
  `https://<name>.duckdns.org:49147`. The app hides on `127.0.0.1:49149`. Caddy's plain-HTTP
  listener sits on 49180, which is not forwarded and never serves the app.

The app itself never binds to anything but localhost; the config refuses any other address.

### Public access (any browser, no Tailscale)

Use this when a device cannot use Tailscale. The paired device cookie becomes the only key: there is
no network identity in front of it. Clients that fail authentication ten times within ten minutes
are locked out for fifteen. HTTPS comes from a Let's Encrypt certificate that Caddy obtains and
renews through a DuckDNS DNS challenge, so no port other than 49147 is needed.

1. Sign in at duckdns.org, add a subdomain, and keep its token **out of chats and repositories**:
   `install -m 600 /dev/null ~/.config/resirch-killalot/duckdns.env && echo 'DUCKDNS_TOKEN=<token>' > ~/.config/resirch-killalot/duckdns.env`
2. In the config, set `[web] access = "public"` and `[web] port = 49149`, and add
   `[public] domain = "<name>.duckdns.org"` and `port = 49147`. Remove `public_base`; it is derived.
3. If `tailscale serve` was publishing 49147, turn it off: `tailscale serve --https=49147 off`.
4. `systemctl --user restart resirch-killalot.service`, then `killalot public-setup`. It downloads
   Caddy with its DuckDNS module into `<store root>/proxy/`, renders the Caddyfile, installs
   `killalot-caddy.service` and the five-minute `killalot-duckdns.timer` (which keeps the name on
   the hub's current IP), and starts both.
5. Forward TCP 49147 on the router to the hub. Then open `https://<name>.duckdns.org:49147/healthz`
   from a phone on mobile data. Some routers cannot loop back to their own public address, so a
   test from inside the home network may fail even when it works from outside.

## 4. Pair each device

```bash
killalot device add "phone"
```

Open the printed link on that device within ten minutes. It works once. On a phone, add the page
to the home screen to use it like an app. `killalot device list` shows the paired devices;
`killalot device revoke <id>` (or the Devices tab) cuts one off immediately. A paired device's
cookie is renewed each time the app opens.

## 5. Session-start hooks

The core plugin ships a session-start hook that both hosts run. It prints the open items of the
tagged project a session starts in, and prints nothing anywhere else.

- **Claude Code** runs it as soon as the plugin is installed.
- **Codex** shows it as a new hook needing review: trust it once in the hooks review. It may ask
  again after a core update.

## 6. Talk to the list: the hub assistant

The web app's **Chat** tab, the "Tell Killalot…" box on the inbox, and Telegram hand your words
to a headless agent on the hub. Whoever holds one of those channels reaches it, so it is confined
in code, not only by its instructions:

- **List only.** Of the list's operations it sees just reading, add, edit, link, unlink and
  comment. Accept, reject, answer, snooze, drop, done and park stay yours: the buttons, `/inbox` and
  the web app. Every change it makes is **staged**: you see it under the reply with Confirm and
  Cancel, and nothing changes until you tap. It never approves and never starts work in a project.
- **Nothing else.** It has no shell, files, web or other connectors, and it does not load your own
  settings, hooks, plugins or CLAUDE.md / AGENTS.md. Claude runs with no built-in tools and no
  setting sources. Codex runs under its own Codex home with the shell, apps, plugins, browser and
  agents turned off and a read-only sandbox; it keeps only Codex's code-mode isolate (no files, no
  network), which it needs to call the list's tools.
- **On topic.** Anything that is not about the list (questions, maths, code, chat) gets one fixed
  line: "I only keep your ReSirch Killalot list…".
- **Capped.** At most 2000 characters a message, 8 model turns a message (Claude), and
  `daily_limit` messages a day across all channels; past that, the buttons and the web app still
  work.

It asks when the project, date or kind is unclear, and conversations continue until you start a
new one. When these rules change, open conversations start fresh.

```toml
[assistant]
backend = "claude"     # or "codex"; the CLI must be installed and signed in on the hub
# command = "/abs/path/to/claude"   # if the service's PATH does not find it
# model = "<model id>"              # optional
timeout_s = 180
daily_limit = 150      # messages a day, all channels together
```

The service runs it as you. Claude uses your existing Claude Code sign-in and plan. Codex runs
under its own home, `<store>/assistant/codex-home`, so sign it in there once:
`CODEX_HOME=<store>/assistant/codex-home codex login` (`killalot doctor` checks it).
Try it from the terminal first: `killalot assistant ask "what is open in <project>?"`, then
`killalot assistant pending` and `killalot assistant confirm <id>`.

## 7. Telegram

1. In Telegram, talk to @BotFather, `/newbot`, and copy the token. Keep it **out of chats and
   repositories**:
   `install -m 600 /dev/null ~/.config/resirch-killalot/telegram.token && echo '<token>' > ~/.config/resirch-killalot/telegram.token`
2. Add to the config: `[telegram]` with `bot_token_file = "~/.config/resirch-killalot/telegram.token"`,
   then `systemctl --user restart resirch-killalot.service`.
3. `killalot telegram link` prints `/start <code>`. Within ten minutes, send it to your bot from
   your own Telegram account. From then on the bot answers only that chat.

The bot sends each new proposal, item waiting on you, due reminder and approval once, with buttons
(Accept, Reject…, Answer…, Done, Snooze, Open). More than five at once arrive as one summary.
Approvals only get an Open button: you approve on the web page that shows the whole dry-run.
Commands: `/inbox`, `/projects`, `/digest` (moves the digest mark), `/new` (fresh conversation).
Anything else you write goes to the assistant. `killalot telegram status` and `unlink` manage it.

## 8. Connect a chat app (claude.ai, ChatGPT)

Needs public access (section 3). The app becomes a client of the list: it reads it and makes
changes as you, applied at once (the chat app's own tool-approval prompt is the confirmation). It
can never approve.

- **claude.ai**: Settings → Connectors → Add custom connector, URL
  `https://<name>.duckdns.org:49147/mcp`. Add it in a browser you paired (section 4); the consent
  page answers only a paired browser. If it is not paired, the page asks for a pairing code from
  `killalot device add "<name>"`. Connectors added on the web are available in the mobile app.
- **ChatGPT**: Settings → Apps & Connectors → Advanced → Developer mode, then create a connector
  with the same URL.

**Chat apps connect only to port 443.** If the router cannot forward 443 (for example, an ISP that
shares one IPv4 among customers and gives each a port block), publish the app on 443 with
Tailscale Funnel and tell Killalot that address. The web app and Telegram keep the public address.

1. `tailscale funnel --bg --https=443 http://127.0.0.1:<[web] port>` (the first time it prints a
   link to allow Funnel for this machine in the tailnet policy).
2. In the config: `[mcp]` with `public_base = "https://<hub>.<tailnet>.ts.net"`, then restart the
   service.
3. Use `https://<hub>.<tailnet>.ts.net/mcp` as the connector URL. The consent page there is a new
   site for the browser: pair it once with the code from `killalot device add "<name>"`.

Only the redirect URLs in `[mcp] redirect_uris` may register (the defaults cover claude.ai and
ChatGPT). `killalot connection list` and `killalot connection revoke <id>` (or the Devices tab)
manage connections; revoking the device that approved a connection ends it too.

## Backups

`serve` copies the store to `<store root>/backups/killalot-YYYYMMDD.db` once a day and keeps
fourteen. To restore one, stop the service, copy the backup over `killalot.db`, and start it again.
