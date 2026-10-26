---
name: service-hub
description: Run one web entry point per machine that lists every hosted web service on a landing page and serves each at http://<host>:<port>/<service>/ through a token-gated reverse proxy, so the backends listen on loopback only. Use when the user wants to put dashboards, viewers or other web apps behind one port or URL, add, remove or rename a service on the hub, asks which services a machine hosts or whether they are up, or needs a service's hub URL; also to install or troubleshoot the hub service. Do not use to build the services themselves or to expose a machine to the internet beyond its existing network setup.
---

# service-hub

`scripts/hub.py` is a stdlib HTTP gateway. One process per machine, on one port:

- `/` is the landing page (`assets/hub.html`): one card per service with its icon, description,
  route, live up/down state and latency (re-probed every 10 s), a filter (`/`), number keys `1`–`9`
  to open a service, and a light/dark/auto theme.
- `/<service>/...` is proxied to that service's loopback backend with the prefix stripped:
  `/board/api/board` reaches `http://127.0.0.1:<backend port>/api/board`. `/<service>` redirects to
  `/<service>/`. Request bodies, streaming responses, gzip, `ETag`/304 and WebSocket upgrades pass
  through; `Location` headers and cookie paths are rewritten under the prefix; a dead backend
  answers 502 with the service's name.
- A **link** service is a card that opens its own URL, for apps that cannot live under a path
  prefix.
- `/api/services` is the JSON behind the page; `/healthz` answers `ok` without a token.

Configuration, install and the backend contract: [references/configuration.md](references/configuration.md).

## Security model

- The hub checks its token for every network client before anything reaches a backend
  (`?token=` once, then an HttpOnly cookie for a year; or `Authorization: Bearer`, or
  `X-Hub-Token`). A browser without it gets a sign-in form. The token is removed from the URL by a
  redirect and is stripped, with the hub cookie and a hub bearer header, from what a backend sees.
- Backends see every proxied request as coming from `127.0.0.1`. A backend that trusts loopback
  clients is therefore only safe when it **listens on loopback** and the hub is the only way in
  from the network. Move each proxied backend to `127.0.0.1` when it joins the hub.
- The hub refuses a non-loopback `bind` without a token. The token travels in clear over plain
  HTTP: treat it as a secret for your own devices, and rotate it by replacing the registry value
  and restarting the service.

## Commands

```bash
python3 scripts/hub.py status [--json]      # probe every service; exit 1 if any is down
python3 scripts/hub.py url <service> [path] # local and public hub URL of a service (or a path under it)
python3 scripts/hub.py token                # a fresh access token
python3 scripts/hub.py serve [--port N] [--bind ADDR] [--quiet]   # what the service runs
```

Set `RIGSYNC_REGISTRY` or pass `--registry` to use another registry.

## Adding a service

1. Confirm the backend works under a path prefix (see the backend contract in the reference).
   If it does not, add it as a `link` service instead.
2. Add a `[hub.services.<name>]` table; `<name>` becomes the URL segment.
3. Move the backend to `127.0.0.1` on its own port and point `url` at it.
4. `systemctl --user restart service-hub.service`, then `hub.py status` and open the card.

Report each service's hub URL from `hub.py url`, never a raw backend port.

## Hard limits

- Never proxy a backend that still listens on the network: it would stay reachable around the hub.
- Never set `bind` to a non-loopback address without a token, and never forward the hub port from
  a router without one.
- The hub reports and forwards; it never changes a backend's data.
