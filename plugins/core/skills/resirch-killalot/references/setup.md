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
`killalot device revoke <id>` (or the Devices tab) cuts one off immediately.

## 5. Session-start hooks

The core plugin ships a session-start hook that both hosts run. It prints the open items of the
tagged project a session starts in, and prints nothing anywhere else.

- **Claude Code** runs it as soon as the plugin is installed.
- **Codex** shows it as a new hook needing review: trust it once in the hooks review. It may ask
  again after a core update.

## Backups

`serve` copies the store to `<store root>/backups/killalot-YYYYMMDD.db` once a day and keeps
fourteen. To restore one, stop the service, copy the backup over `killalot.db`, and start it again.
