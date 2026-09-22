# Plot server

`scripts/plot_server.py` is a stdlib, read-only HTTP server that makes every project's `plots/`
viewable in a browser without copying files and without any file-size limit. Run one instance, as
a systemd user service, on the machine where plots are rendered (`rig-4090`).

## What it serves

- A plot's URL path is its **absolute file path**:
  `http://<host>:<port>/mnt/<volume>/.../<project>/plots/<experiment_path>/<script_stem>/<leaf>.html`.
  Any tool can build the URL from the path alone.
- Only files inside `<project>/plots/` are served, where `<project>` is a git checkout (it holds a
  `.git`) under one of the configured `roots`, at most `depth` levels below that root. Everything
  else, including code, evaluations and checkpoints, answers 404. `..` segments are refused, and a
  symlink inside `plots/` cannot reach outside it. `plots/` itself may be a symlink to another
  volume.
- `/` is the plot browser (`assets/plot-browser.html`, read on every request): a project picker,
  the project's `plots/` as a folder tree (single-child folder chains merged into one row) or as a
  newest-first list, a filter box, and the selected plot beside it with its breadcrumb, render time,
  size, reload, copy-link and open-alone buttons. The list hides and shows from the header
  button or `b`. Opening a plot shows a progress panel: the download (MB of the uncompressed size,
  percent, speed, time left), then the browser drawing it (elapsed time). The page downloads the
  file once and hands those bytes to the plot frame, with a `<base>` so the plot's relative links
  still resolve on the server. The address bar carries `#p=<project>&f=<plot>`,
  so a copied link reopens the same view. A green dot marks plots new or re-rendered since this
  browser last opened them, the list rescans every 30 s, and an open plot that gets re-rendered
  offers a reload. Keys: `/` filter, `↑`/`↓` or `j`/`k` previous/next plot, `Esc` clear, `r` reload,
  `b` show/hide the list.
  On a narrow screen the list slides in from the menu button. View preferences stay in the browser.
  Without the page file, `/` falls back to the plain list, which is always at `/plain`.
- `/api/projects` and `/api/files?project=<absolute project path>` are the JSON behind the page.
  Project discovery is cached for 30 s.
- `/healthz` answers `ok` without a token.
- Responses carry `Cache-Control: no-cache` plus an `ETag` (answered with 304 when unchanged), so
  reloading after a rerender shows the new file and an unchanged one is not sent again.
- Text files of 64 KB or more are gzip-compressed on the fly for clients that accept it (plot HTML
  shrinks about 10×). `X-Plot-Size` carries the uncompressed size.
- The server never writes anything.

## Configuration: the `[plots]` table

Put it in the rigsync user registry `~/.config/rigsync/machines.toml` (per user, not committed)
on the serving machine:

```toml
[plots]
roots      = ["/mnt/<volume A>/Projects", "/mnt/<volume B>/Projects"]  # searched for checkouts with plots/
port       = 40975                              # default 40975
bind       = "0.0.0.0"                          # default 127.0.0.1 (this machine only)
token      = "<output of plot_server.py token>" # required whenever bind is not loopback
public_url = "http://<public address>:40975"    # optional; `url` also prints the public URL
depth      = 4                                  # optional; how deep checkouts sit below a root
```

The server refuses to start on a non-loopback `bind` without a `token`.

## Access

- **From this machine or through an SSH connection to it** (`127.0.0.1`): no token. A loopback
  client has already authenticated to the machine. This includes an IDE browser tab that routes
  through the IDE's SSH connection, and a plain tunnel: `ssh -N -L 40975:127.0.0.1:40975 rig-4090`
  then open `http://127.0.0.1:40975/`.
- **From the network** (LAN, private overlay, or a router port forward): every path except
  `/healthz` answers 401 unless the request carries the token as `?token=<token>` or
  `Authorization: Bearer <token>`. Opening any page once with `?token=` sets an HttpOnly cookie
  for a year, so bookmark `http://<address>:40975/?token=<token>` once and every later plot link
  works without it.
- The token travels in clear over plain HTTP. Treat it as a secret for your own devices, avoid
  opening the public URL on untrusted networks, and rotate it by replacing the registry value and
  restarting the service. Never publish the port without a token.

## Install sandboxed (recommended whenever the port is reachable from the network)

The server's own path checks keep it inside `plots/`, but a user service runs as you and could
read everything you can. Unprivileged sandboxing is unavailable on hosts that restrict user
namespaces (Ubuntu 24.04 does), so the sandboxed install is a system service and needs `sudo` once:

```bash
sudo bash <skill>/scripts/install_sandboxed.sh --new-token   # --new-token rotates the token; omit to keep it
```

It reads your `[plots]` table, then:

- creates the `plotserver` system account (no login, no home);
- installs a root-owned copy of the server in `/usr/local/lib/plot-server/`;
- writes `/etc/plot-server/plot-server.toml` (mode 0640, root:plotserver) with the token and
  `manifest = "/etc/plot-server/projects.txt"`;
- installs `plot-server.service` (from `assets/plot-server.sandboxed.service`) plus
  `plot-server-sync.service` and `.timer`, replaces your user-level service, starts everything,
  and prints the bookmark.

What the sandbox enforces, whatever the server's code does:

- **Only `plots/` exists.** `/home`, `/root` and `/mnt` are empty; `/media`, `/srv`, `/opt`, most of
  `/var` and `/boot` are hidden. The root sync job bind-mounts each project's `plots/` back in,
  read-only. Code, evaluations, checkpoints, `.git`, SSH keys and your registry are not reachable.
- **Nothing is writable.** The whole filesystem is read-only to the service (`ProtectSystem=strict`
  plus read-only binds); `/tmp` is private.
- **No privilege.** It runs as `plotserver` with no capabilities, `NoNewPrivileges`, no setuid,
  a system-call allow-list, no namespaces, and only IP and local sockets.
- **Bounded resources.** 1 GB of memory, 128 tasks, two CPU cores.

`plot-server-sync.timer` runs `plot_server.py sync` as root every 2 minutes: it discovers checkouts
with `plots/` under the roots, refuses a `plots/` that resolves outside every root or whose path a
unit file cannot carry safely (spaces, quotes, `:` or `%`), writes the bind list to
`/etc/systemd/system/plot-server.service.d/plots.conf` and the project list to the manifest, and
restarts the server only when the list changed. A new project's plots appear within 2 minutes;
`sudo systemctl start plot-server-sync.service` publishes them at once.

Re-run the install script after a release that changes `plot_server.py` or `plot-browser.html`:
the service runs its root-owned copy, not the skill checkout. Useful commands:

```bash
journalctl -u plot-server -f                          # access log: client, method, path, status, auth
journalctl -u plot-server | grep -E 'auth=(REJECTED|missing)'   # refused network requests
sudo grep token /etc/plot-server/plot-server.toml     # the current token
sudo systemctl stop plot-server                       # emergency stop
systemd-analyze security plot-server                  # systemd's own exposure score
```

Each access-log line is `<client> <method> <path> <status> auth=<loopback|token|missing|REJECTED|open>`.
The query string is never logged, so a `?token=` never lands in the journal.

## Install as a user service (no sudo, no sandbox)

`assets/plot-server.service` is a systemd user unit template. Point it at a **stable** copy of
the script, such as this repository's checkout on the machine, and re-point it if that path moves:

```bash
python3 <skill>/scripts/plot_server.py token     # paste the output into [plots] token
mkdir -p ~/.config/systemd/user && cp <skill>/assets/plot-server.service ~/.config/systemd/user/plot-server.service
sed -i "s|__PLOT_SERVER_PY__|<absolute path to this skill's scripts/plot_server.py>|" ~/.config/systemd/user/plot-server.service
systemctl --user daemon-reload && systemctl --user enable --now plot-server.service
loginctl enable-linger "$USER"     # keep it running with no login session
systemctl --user status plot-server.service --no-pager
```

Restart it after editing `[plots]` or after a skill release that changes the script:
`systemctl --user restart plot-server.service`. Logs: `journalctl --user -u plot-server -f`.

## Commands

```bash
python3 <skill>/scripts/plot_server.py url <plot file>   # local and public URL, and whether the server answers
python3 <skill>/scripts/plot_server.py list              # discovered projects and their plots
python3 <skill>/scripts/plot_server.py token             # a fresh token
python3 <skill>/scripts/plot_server.py serve             # run in the foreground (what the unit runs)
```

`url` exits 2 when the file is not servable (outside every project's `plots/`), 3 when the server
is not answering, and 4 when the server answers but does not serve this file yet (a sandboxed
server before its sync job has published the project). It prints the URLs in every case but 2.
