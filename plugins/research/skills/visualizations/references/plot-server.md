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
- The one exception is each experiment's run-identity records, and only through `/api/runs`: the
  derived `evaluations/<experiment_path>/RUN_ID_MAP.json`, and the tracked identity files
  `config/<experiment_path>/RUN_ID_VERSIONS.json` and `config/<experiment_path>/RUN_ID_HISTORY.json`,
  nothing else. The file name must match `RUN_ID_(MAP|VERSIONS|HISTORY).json` exactly, the
  experiment path is at most 4 plain segments (no `..`, no hidden folders), and a symlink cannot
  lead outside the real `evaluations/` (for the map) or the real `config/` (for the other two).
  Every other file under `evaluations/` and `config/` (`.run_config.json`, `.status.json`,
  metrics, the YAML configs) answers 404, by path and through the API.
- **What the Runs view exposes.** `RUN_ID_MAP.json` embeds each run's full resolved config under
  `configs` (plus copies of the versions and history), so anyone who can open the page can read
  every run's hyperparameters and config values. `.env` never enters a resolved config, so secrets
  are not part of it; still treat the configs as exposed to every page viewer.
- `/` is the plot browser (`assets/plot-browser.html`, read on every request): a project picker,
  the project's `plots/` as a folder tree (single-child folder chains merged into one row) or as a
  newest-first list, a filter box, and the selected plot beside it with its breadcrumb, render time,
  size, reload, copy-link, download and open-alone buttons. Download (or `d`) saves the plot file
  exactly as rendered, under its leaf name, ready to send to someone. The list hides and shows
  from the header button or `b`. Opening a plot shows a progress panel: the download (MB of the uncompressed size,
  percent, speed, time left), then the browser drawing it (elapsed time). The page downloads the
  file once and hands those bytes to the plot frame, with a `<base>` so the plot's relative links
  still resolve on the server; the base is marked so the plot's own download buttons leave it
  out. The address bar carries `#p=<project>&f=<plot>`, so a copied link reopens the same view. A green dot marks plots new or re-rendered since this
  browser last opened them, the list rescans every 30 s, and an open plot that gets re-rendered
  offers a reload. Keys: `/` filter, `↑`/`↓` or `j`/`k` previous/next plot, `Esc` clear, `r` reload,
  `d` download, `b` show/hide the list, `f` focus (the plot alone fills the window; `f` or `Esc`
  leaves).
- Every plot shows the reader toolbar of `assets/plot-shell.js` (hide/show the run selection,
  zoom, pan, hover, legend, log y, fit, PNG/SVG, fullscreen; `?` inside a plot lists its keys). A
  plot rendered before the toolbar existed gets the server's copy, served at `/plot-shell.js`,
  injected into the frame after the `<base>`; the file on disk and the browser's own download stay
  untouched, but the plot's own download buttons then save it with the toolbar inside. Keys the
  browser owns (`j`/`k`, arrows, `r`, `d`, `b`, `/`, `f`, `Esc`) still work while the plot has
  the keyboard: the toolbar passes them up.
  On a narrow screen the list slides in from the menu button. View preferences stay in the browser.
- The header's **Plots / Runs** toggle switches to the Runs view: hash ↔ params for one experiment.
  - Pick an experiment: any folder under `evaluations/`, nested ones included, that holds a
    `RUN_ID_MAP.json`, or under `config/` that holds a `RUN_ID_VERSIONS.json`.
  - The table has one row per run, with columns for the hash (shortened, full on hover; click to
    copy), the identity version, one column per identity param of the current version, wave, rig
    and state. **all config params** adds a column for every key of the runs' resolved configs
    (nested keys dotted, as in `optim.lr`).
  - The free filter takes words and `key=value` terms, all ANDed: `lr=1e-4 model=vit seed=0`.
    Numbers compare by value, so `1e-4` matches `0.0001`. A key can name a column, an identity
    param, or a config key, including by its last dotted part. Each column also has its own filter:
    a substring, or `=x` for an exact match. Clicking a header sorts by that column.
  - The lookup box takes a hash or a prefix (with or without `rid-`). An old hash from before a
    run_id migration resolves through `RUN_ID_HISTORY.json` and shows as `old → current` with a
    `migrated vN → vM` badge. When the `VERSIONS` or `HISTORY` endpoint answers 404 (an older
    sandbox bind list, or a checkout without the tracked files), the page falls back to the
    `versions` and `history` copies embedded in the map.
  - Each row can copy its hash or its full config JSON. Its WandB link appears when the config
    records a WandB URL. **plots** opens the Plots view filtered to `plots/<experiment_path>/`:
    plots select runs by hash inside the file, never in the path.
  - Maps from older run_id layouts get a limited view: the run path or hash and the params
    recoverable from it.
  - The address bar carries `#v=runs&p=<project>&e=<experiment>&q=<filter>`; `#p=&f=` plot links
    work as before. In the Runs view `/` focuses the run filter, `Esc` clears it, and the plot
    keys (`r`, `d`, `j`/`k`, arrows, `b`) are off.
  Without the page file, `/` falls back to the plain list, which is always at `/plain`.
- `/api/projects` and `/api/files?project=<absolute project path>` are the JSON behind the page.
  Project discovery is cached for 30 s.
- `/api/experiments?project=<absolute project path>` lists the project's experiments, each with the
  `RUN_ID_*` files it holds and their size and mtime, as `{"experiments": [{"path", "files": {"MAP":
  {"size", "mtime"}, ...}}]}`. An experiment is the union of every folder under `evaluations/` that
  directly holds a `RUN_ID_MAP.json` and every folder under `config/` that directly holds a
  `RUN_ID_VERSIONS.json`, at most 4 levels deep. It skips hidden and run folders and visits at most 5000 folders per
  project. It is cached for 30 s.
- `/api/runs?project=<path>&experiment=<experiment_path>&file=MAP|VERSIONS|HISTORY` serves that raw
  file (`file` defaults to `MAP`; `MAP` resolves under `evaluations/`, `VERSIONS` and `HISTORY`
  under `config/`), with the same ETag, 304 and gzip handling as plots. Any other
  name, experiment or project answers 404. Both endpoints need the token off this machine, like
  every other path.
- `/plot-shell.js` serves `assets/plot-shell.js` for the browser to inject; it needs the token off
  this machine like every other path.
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

- **Only `plots/` and the run-identity files exist.** `/home`, `/root` and `/mnt` are empty;
  `/media`, `/srv`, `/opt`, most of `/var` and `/boot` are hidden. The root sync job bind-mounts each
  project's `plots/` back in, read-only. It also bind-mounts each experiment's
  `evaluations/<experiment_path>/RUN_ID_MAP.json` and `config/<experiment_path>/RUN_ID_{VERSIONS,HISTORY}.json`,
  read-only, as single files, never the folder. Code, the rest of `evaluations/` and `config/`
  (`.run_config.json`, `.status.json`, metrics, YAML), checkpoints, `.git`, SSH keys and your
  registry are not reachable. The map's embedded `configs` (every run's resolved config, never
  `.env`) are readable through the Runs view; that is the one config data the sandbox exposes.
- **Nothing is writable.** The whole filesystem is read-only to the service (`ProtectSystem=strict`
  plus read-only binds); `/tmp` is private.
- **No privilege.** It runs as `plotserver` with no capabilities, `NoNewPrivileges`, no setuid,
  a system-call allow-list, no namespaces, and only IP and local sockets.
- **Bounded resources.** 1 GB of memory, 128 tasks, two CPU cores.

`plot-server-sync.timer` runs `plot_server.py sync` as root every 2 minutes: it discovers checkouts
with `plots/` under the roots and their experiments' `RUN_ID_*.json` files, refuses a `plots/` or
file that resolves outside every root (or a file outside its `evaluations/` or `config/`) or whose path a
unit file cannot carry safely (spaces, quotes, `:` or `%`), writes the bind list to
`/etc/systemd/system/plot-server.service.d/plots.conf` and the project list to the manifest, and
restarts the server only when the list changed. A new project's plots, and a new experiment's
`RUN_ID_*.json`, appear within 2 minutes. A RUN_ID file rewritten in place shows at once; one
replaced by a rename gets a new inode, which the next sync detects and re-binds with a restart;
`sudo systemctl start plot-server-sync.service` publishes them at once.

Re-run the install script after a release that changes `plot_server.py`, `plot-browser.html` or
`plot-shell.js`:
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
