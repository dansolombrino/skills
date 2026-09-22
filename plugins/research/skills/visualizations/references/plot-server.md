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
- `/` lists every discovered project and its HTML plots, newest first. Discovery is cached for 30 s.
- `/healthz` answers `ok` without a token.
- Responses carry `Cache-Control: no-cache`, so reloading the tab after a rerender shows the new file.
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

## Install as a user service

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

`url` exits 2 when the file is not servable (outside every project's `plots/`) and 3 when the file
is servable but the server is not answering. It still prints the URLs in that case.
