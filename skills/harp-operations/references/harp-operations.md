<!--
  - SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Running and debugging HaRP

A detailed runbook, part of the [harp-operations](../SKILL.md) skill.

HaRP (`nextcloud/HaRP`) is the recommended AppAPI deploy daemon. This page is about the service itself: how to
run it, how to prove it is healthy, how to read what it logs, and how it fails. Registering it as a daemon and
managing ExApps with `occ` belongs to
[operations.md](../../exapp-operations/references/operations.md).

Last verified against: HaRP 0.4.3, AppAPI 35.0.0-dev.1, Nextcloud master (35), on 2026-08-11.

## The three planes

One container, three independent jobs. Knowing which one broke is most of the debugging.

| Plane | What it is | Default port | Fails as |
|---|---|---|---|
| ExApp traffic | HTTP(S) frontend serving `/exapps/...`; authorises every request against Nextcloud and rewrites the ExApp auth headers | `8780` (HTTP), `8781` (HTTPS) | `401`, `403`, `404`, `503` |
| Tunnel | FRP server that ExApp containers, and remote Docker engines, dial back into | `8782` | installs that end in "heartbeat check failed" |
| Control | The Docker Engine or Kubernetes API, proxied for AppAPI under `/exapps/app_api/<docker-api-path>` | via the mounted socket | deploys that fail at pull or create |

Two consequences worth internalising:

- **AppAPI reaches ExApps through the public URL.** For a HaRP daemon the ExApp address is
  `<nextcloud_url>/exapps/<appid>`, so the install-time `/heartbeat`, `/init` and `/enabled` calls go through
  your reverse proxy exactly like browser traffic does. No `/exapps/` rule means no installs (see
  [operations.md Quickstart](../../exapp-operations/references/operations.md#2-quickstart-zero-to-a-working-exapp)).
- **HaRP synthesises the ExApp credentials.** Browser requests arrive at the ExApp with `EX-APP-ID`,
  `EX-APP-VERSION` and `AUTHORIZATION-APP-API` set by HaRP, plus `AA-VERSION` hardcoded to `32` in the
  generated HAProxy config. ExApps must therefore never validate `AA-VERSION` strictly.

## Deploying HaRP

```bash
docker run -d \
    --name appapi-harp -h appapi-harp \
    --restart unless-stopped \
    --network <nextcloud-docker-network> \
    -e HP_SHARED_KEY="<STRONG_ASCII_SECRET>" \
    -e NC_INSTANCE_URL="<nextcloud-url-reachable-from-harp>" \
    -e HP_TRUSTED_PROXY_IPS="<proxy-cidr>" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v harp-certs:/certs \
    -p 127.0.0.1:8780:8780 \
    -p 8782:8782 \
    ghcr.io/nextcloud/nextcloud-appapi-harp:release
```

- `/certs` **must be a persistent volume**. It holds the generated FRP certificate authority; losing it forces
  a reinstall of every ExApp (see [Certificates](#certificates-and-the-frp-tunnel)).
- The Docker socket gives HaRP root-equivalent control of the host. Run it only on a host you trust, from the
  official image.
- Publish `8780` on a public interface only if something else terminates TLS in front of it. `8782` must be
  reachable by ExApp containers and by any remote Docker engine.
- Nextcloud AIO manages its own HaRP; do not run a second one
  ([aio.md](../../exapp-operations/references/aio.md)).

## Probes: what healthy looks like

Run these before changing anything. `<key>` is `HP_SHARED_KEY`.

| Probe | Command | Healthy answer |
|---|---|---|
| Version and capabilities | `curl -s -H "harp-shared-key: <key>" http://<harp>:8780/exapps/app_api/info` | `{"version": "0.4.3", "docker": true, "kubernetes": {...}}` |
| Container health | `docker inspect <harp> --format '{{.State.Health.Status}}'` | `healthy` (built-in healthcheck, 10s interval, 9 retries) |
| Frontend liveness | `curl -o /dev/null -w '%{http_code}' http://<harp>:8780/` | `404`, by design. Not a health signal, only proof something answers |
| Public path | `curl -o /dev/null -w '%{http_code}' <nextcloud-url>/exapps/app_api/info` | `401`. `404` means the request fell through to Nextcloud, `502` means the proxy rule points nowhere |
| Auth | the same `/info` call **without** the header | `401` |

An unsigned `401` is a pass, not a failure: it proves the request reached HaRP and was rejected for the right
reason.

## Configuration that matters

Full list with defaults: the Environment Variables section of the
[HaRP README](https://github.com/nextcloud/HaRP). The ones an operator actually touches:

| Variable | Default | Set it when |
|---|---|---|
| `HP_SHARED_KEY` (or `HP_SHARED_KEY_FILE`) | required | Always. Must equal the daemon's `--harp_shared_key` byte for byte, ASCII only |
| `NC_INSTANCE_URL` | required | Always. The Nextcloud this HaRP serves, reachable from inside the container |
| `HP_TRUSTED_PROXY_IPS` | `""` | HaRP sits behind another reverse proxy (see below) |
| `HP_EXAPPS_ADDRESS` / `HP_EXAPPS_HTTPS_ADDRESS` | `0.0.0.0:8780` / `0.0.0.0:8781` | Port conflicts, or binding to one interface |
| `HP_FRP_ADDRESS` | `0.0.0.0:8782` | Port conflicts; must stay reachable by ExApps |
| `HP_LOG_LEVEL` | `warning` | `info` or `debug` while diagnosing, then back |
| `HP_TIMEOUT_CONNECT` / `HP_TIMEOUT_CLIENT` | `30s` | Slow or congested networks |
| `HP_TIMEOUT_SERVER` | `1800s` | Practically never: long AI requests need it. Lowering it truncates them |
| `HP_SESSION_LIFETIME` | `3` | Tuning how long a Nextcloud session is cached (0 disables caching) |
| `HP_BLACKLIST_COUNT` / `HP_BLACKLIST_WINDOW` | `10` / `300` | Tuning how many bad status codes ban an IP, and for how long |
| `HP_WATCHDOG_ENABLED` / `_INTERVAL` / `_FAILS` | `true` / `10` / `12` | Tuning agent supervision (see [Load](#load-timeouts-and-self-healing)) |
| `HP_FRP_DISABLE_TLS` | `false` | Only when TLS for the tunnel is terminated by something else on a trusted network |
| `HP_K8S_*` | unset | Kubernetes mode: [kubernetes.md](../../exapp-operations/references/kubernetes.md) |

## Behind another reverse proxy

Without `HP_TRUSTED_PROXY_IPS`, HaRP sees your proxy's address as the client address, so per-IP behaviour
(rate limiting, the blacklist) applies to the proxy instead of the real caller. Set it to the proxy's address
or CIDR.

Two shapes that used to break silently and are accepted since 0.4.3: values arriving with literal quotes (from
`--env-file` files or compose `environment:` list entries), and ranges written with host bits set such as
`192.168.100.20/24`. Invalid entries are skipped individually rather than discarding the whole list. On older
images, either mistake disabled client IP detection entirely, so upgrade before debugging this.

## Reading the logs

`docker logs <harp>` interleaves three formats. Recognising them saves the most time.

**HAProxy access lines** carry the frontend, the backend, timers and the status:

```
haproxy[76]: 172.20.0.10:37058 [11/Aug/2026:07:35:12.471] ex_apps docker_engine_backend/frp_server 0/0/0/1/1 404 262 ...
    "GET /exapps/app_api/v1.44/images/example.local/minimal_exapp:1.0.0-cpu/json HTTP/1.1"
```

- `ex_apps` is the frontend, the pair after it is `backend/server`.
- `ex_apps/<NOSRV>` means no backend matched: the app is not deployed on this HaRP daemon (a manual-install
  app, or a different daemon).
- `docker_engine_backend/frp_server` is AppAPI driving the Docker Engine through HaRP. The `...-cpu` request
  above is normal: AppAPI probes a `:<image-tag>-cpu` variant before the exact tag.

**Agent lines** are the request authoriser:

```
[2026-08-11T07:35:12+0000] [DEBUG] Incoming request to ExApp: path=/exapps/minimal_exapp/echo, headers=...
[2026-08-11T07:35:12+0000] [INFO] Container 'nc_app_minimal_exapp' does not exist.
```

**frpc lines** live in the *ExApp's* logs, not HaRP's. A healthy tunnel prints `login to server success` and
`start proxy success` in `docker logs nc_app_<appid>`; their absence is the classic "heartbeat check failed"
cause.

### Status codes as a decision table

| Code | Meaning |
|---|---|
| `401` | Shared key missing or wrong on a control route, or an unsigned request to `/exapps/app_api/...` |
| `403` | The caller's level is below the route's `access_level` (anonymous on a USER or ADMIN route) |
| `404` | Path not declared by the ExApp, or the app is not routable here (`<NOSRV>`), or `GET /` |
| `405` | No route of that path lists the method in its `verb` (HaRP 0.4.8+); the `Allow` header names the listed ones |
| `502` | An infrastructure route (`/heartbeat`, `/init`, `/enabled`) requested from outside, which is blocked by design, or a reverse-proxy rule pointing at nothing |
| `503` | No healthy backend: the agent is stalled or overloaded |

## Upgrades and versions

- Tags, from HaRP's publish workflow: `:release` on every GitHub release, `:vX.Y.Z` alongside it, `:latest`
  and `:main` on every push to `main`. Production wants `:release` or a pinned `:vX.Y.Z`; `:latest` is a
  development build.
- AppAPI enforces a floor (`Application::MINIMUM_HARP_VERSION`) and reports it through the `HarpVersionCheck`
  setup check: `0.4.8` with nextcloud/app_api#1149 and its backports to NC33, NC34 and NC35, `0.3.0` before.
  With #1149 the check caches the version it reads for an hour, so the warning can outlive a HaRP update by
  that long.
- Before 0.4.3, `/info` reported the version as a float, which Nextcloud read as `0.4.0` whatever the real
  patch level was. On 0.4.3 and later it is a real `x.y.z` string, so the admin overview shows the truth.
- Upgrading is `docker pull` plus recreate; ExApps reconnect their tunnels on their own. Verify with the
  probe table afterwards, and check the admin setup checks.

## Certificates and the FRP tunnel

- HaRP generates its own FRP certificate authority into `/certs/frp` (`ca.crt`, `client.crt`, `client.key`)
  and TLS for the tunnel is on by default.
- ExApp images receive those certificates **at install time**: AppAPI passes `install_frp_certs` with the
  deploy (`DockerActions`), and the ExApp's `start.sh` writes them into its frpc config when `/certs/frp`
  exists. Because that happens on deploy and not on start, regenerating the certificates (stopping HaRP,
  deleting `/certs/frp`, starting it again) requires you to **remove and reinstall every ExApp**. Restarting
  an ExApp is not enough.
- Remote Docker engines also hold a copy; after regeneration, re-copy `client.crt`, `client.key` and `ca.crt`
  to each engine and restart its `frpc`
  ([remote-daemon.md](../../exapp-operations/references/remote-daemon.md)).
- This is unrelated to Nextcloud's own certificate store, which AppAPI pushes into every ExApp container at
  deploy time ([operations.md](../../exapp-operations/references/operations.md#7-operating-appapi)).

## Load, timeouts and self-healing

The request authoriser is a small agent HAProxy consults for every ExApp request. If it stalls, HAProxy has no
healthy backend and every ExApp turns into `503` at once, which is why a busy host, for example one running
`occ app_api:app:update --all`, could take down ExApp routing entirely.

Since 0.4.3 the agent resolves DNS asynchronously, reuses a pooled Nextcloud session with bounded timeouts,
and is supervised by a watchdog with a `/heartbeat` endpoint, so a stalled or crashed agent restarts instead
of taking traffic down with it. With the default watchdog settings a dead agent is detected in about two
minutes, a hung one in about three.

Operationally: keep `HP_TIMEOUT_SERVER` at its default so long-running AI requests survive, upgrade to at
least 0.4.3 before investigating `503` bursts, and check host CPU pressure at the timestamps of the failures.

## Troubleshooting (symptom first)

| Symptom | Cause and fix |
|---|---|
| Every ExApp request is `401` | Shared key mismatch between `HP_SHARED_KEY` and the daemon's `--harp_shared_key`, or a non-ASCII key. Re-register the daemon with the exact value |
| One app is `404` with `<NOSRV>` in the log | That app is not deployed on this HaRP daemon (manual-install apps are served at `/index.php/apps/app_api/proxy/<appid>/...`) |
| A route answers `403` for normal users | The route declares `access_level` USER or ADMIN; anonymous callers get `403` |
| A request that used to work gets `405` | HaRP 0.4.8+ with an AppAPI that sends the route verbs checks the method: no route of that path lists it, and the reply's `Allow` header names the ones listed. The ExApp's `info.xml` must add the method to that route's `<verb>`; update the ExApp once it does |
| Browser gets `502` on `/heartbeat`, `/init` or `/enabled` | Working as intended: infrastructure routes are not reachable from outside |
| Every ExApp install ends in "heartbeat check failed" | The `/exapps/` rule on the Nextcloud reverse proxy is missing or wrong; AppAPI cannot reach `<nextcloud_url>/exapps/<appid>/heartbeat` |
| Bursts of `503` across all ExApps under load | Agent stall. Upgrade to 0.4.3+, then look for host CPU starvation at those timestamps |
| Client IPs in logs and bans are all the proxy's | `HP_TRUSTED_PROXY_IPS` unset, quoted, or written with host bits on an image older than 0.4.3 |
| `docker ps` shows HaRP `unhealthy` | The built-in healthcheck is failing; read `docker logs` from the start of the container |
| ExApps break right after certificate regeneration | Expected: reinstall each ExApp so it embeds the new certificates |
| A second Nextcloud cannot use this HaRP | `NC_INSTANCE_URL` is a single value and every request is authorised against that instance; run one HaRP per Nextcloud |
| Deploy fails at image pull, `.../images/create ... 500` | Control plane: the image is local-only and the daemon has no `registry ... to local` mapping, or the registry is unreachable |

## Related

- [operations.md](../../exapp-operations/references/operations.md): daemon registration, the ExApp lifecycle
  and the occ surface.
- [remote-daemon.md](../../exapp-operations/references/remote-daemon.md): ExApp containers on another host.
- [kubernetes.md](../../exapp-operations/references/kubernetes.md): HaRP against a cluster.
- [exapp-development.md](../../exapp-development/references/exapp-development.md): the ExApp side of the
  tunnel, including what an image needs to be reachable through HaRP.
- HaRP README: full environment variable reference, remote engine setup, adapting ExApps:
  https://github.com/nextcloud/HaRP
