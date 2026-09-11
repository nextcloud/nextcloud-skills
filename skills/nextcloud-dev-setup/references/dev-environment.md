<!--
  - SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Set up an ExApp development environment

A detailed runbook, part of the [nextcloud-dev-setup](../SKILL.md) skill.

This runbook builds a local Nextcloud development environment for creating and debugging ExApps, based on
[nextcloud-docker-dev](https://github.com/nextcloud/nextcloud-docker-dev): Nextcloud from source, AppAPI, a HaRP
deploy daemon, and a fast manual-install loop. It is written so that an AI coding agent can execute it end to end;
humans are welcome too.

Scope: local development only. Installing AppAPI on a production or existing instance is covered by the
Quickstart in [operations.md](../../exapp-operations/references/operations.md) (section 2). Kubernetes, remote
hosts and AIO have their own runbooks: [kubernetes.md](../../exapp-operations/references/kubernetes.md),
[remote-daemon.md](../../exapp-operations/references/remote-daemon.md),
[aio.md](../../exapp-operations/references/aio.md).

Last verified against: Nextcloud master (36), AppAPI 36.0.0-dev.0, HaRP 0.4.5, nextcloud-docker-dev d0d1016,
on 2026-09-08; Stage 8 with chrome-devtools-mcp 1.7.0 on 2026-08-18.

## If you are an AI agent, read this first

- Execute stages in order; every stage ends with a Verify block. Do not continue past a failed Verify; use the
  matching "If it fails" entry, then re-verify.
- The environment is disposable, but its databases are not yours to destroy: never run `docker compose down -v`
  and never delete `workspace/` or named volumes without explicit human approval.
- Never edit tracked files of the nextcloud-docker-dev checkout for this setup. The whole AppAPI overlay lives in
  files that are untracked by design (`.env`, `docker-compose.override.yml`, `data/nginx/vhost.d/`,
  `data/harp.key`).
- All state is discoverable by command (`docker compose ps`, `./scripts/occ.sh`, `docker logs`); verify state
  rather than assuming it.
- Kill development processes by exact PID only; on setups where ExApps run in Kubernetes their processes are
  visible on the host and a pattern kill can hit them.
- Use dev-only secrets in this environment and never reuse them elsewhere.

### Recommended sandbox

Run all of this inside a disposable Linux VM (or an equivalent isolated box) that the agent fully owns:

- Ubuntu 24.04 or any systemd Linux, 4+ vCPUs, 8+ GB RAM, 40+ GB disk.
- Docker Engine + docker compose v2, git, curl, make, sudo.
- The agent runs inside the sandbox with permission prompts relaxed; the Docker socket equals root, which is
  exactly why it should never be an agent's main machine. Snapshots make mistakes cheap.

Everything below assumes a shell inside that sandbox.

**On a Mac** the stages below apply unchanged - that was verified end to end on Apple Silicon, including the
default `DOMAIN_SUFFIX=.local`. Read [macos.md](macos.md) first anyway: it covers the Docker socket, which
ExApp images exist for Apple Silicon, and the handful of macOS-only gotchas, so you do not spend time on
adjustments that turn out to be unnecessary.

## Stage 1: clone and bootstrap

Goal: nextcloud-docker-dev checked out, `.env` generated, Nextcloud server source, app_api and the notifications
app cloned.

```bash
git clone https://github.com/nextcloud/nextcloud-docker-dev
cd nextcloud-docker-dev
./bootstrap.sh app_api notifications
```

Notes:
- `bootstrap.sh` writes `.env` (project name `master`, domain suffix `.local`, `PROTOCOL=http`, mysql), appends
  the `*.local` hostnames to `/etc/hosts` (needs sudo; a dnsmasq wildcard `address=/.local/127.0.0.1` is the
  alternative), clones `nextcloud/server` (shallow) into `workspace/server` plus a default app set, and because of
  the `app_api notifications` arguments also clones `nextcloud/app_api` and `nextcloud/notifications` into
  `workspace/server/apps-extra/` and adds both to `NEXTCLOUD_AUTOINSTALL_APPS`.
- `notifications` is asked for explicitly because it is not part of the server repository: it is a separate
  repository that ships bundled only in release tarballs, and the app store has no build for a development
  version. Without it, an ExApp's notification calls through AppAPI return 200 and deliver nothing (Stage 3).
- First run only: if `.env` already exists, `bootstrap.sh` validates and exits; arguments do nothing. On an
  existing checkout instead run:
  `git clone https://github.com/nextcloud/app_api workspace/server/apps-extra/app_api` and
  `git clone https://github.com/nextcloud/notifications workspace/server/apps-extra/notifications`, then enable
  both in Stage 3.
- app_api needs no build step for runtime use (built JS is committed; composer/npm are only for developing app_api
  itself, see `AGENTS.md` in the [app_api repository](https://github.com/nextcloud/app_api)).

Verify:

```bash
grep -E "COMPOSE_PROJECT_NAME|DOMAIN_SUFFIX|PROTOCOL" .env
test -d workspace/server/apps-extra/app_api && echo app_api-cloned
test -d workspace/server/apps-extra/notifications && echo notifications-cloned
python3 -c "import socket; print(socket.gethostbyname('nextcloud.local'))"
```

Expected: `COMPOSE_PROJECT_NAME=master`, `DOMAIN_SUFFIX=.local`, `PROTOCOL=http`; `app_api-cloned`;
`notifications-cloned`;
`nextcloud.local` resolving to a loopback address (`127.0.0.1`). An unresolvable name raises
`socket.gaierror`. (The resolution check is written in Python because `getent` does not exist on macOS.)

Note that `bootstrap.sh` calls `scripts/update-hosts`, which needs `sudo` for every hostname it adds. Under
`set -e` a refused `sudo` aborts the whole script before it clones anything, so run it where you can answer
the password prompt.

If it fails:
- `nextcloud.local` does not resolve: re-run `./scripts/update-hosts` with sudo, or set up the dnsmasq wildcard.
- Missing `PROTOCOL=http` (hand-written `.env`): add it, otherwise Nextcloud generates https URLs and plain-http
  flows chase redirects.

## Stage 2: start Nextcloud

Goal: the master instance installed and answering on http://nextcloud.local with admin/admin.

```bash
docker compose up -d nextcloud
```

First start pulls images and auto-installs Nextcloud (admin/admin, plus users like user1/user1, jane/jane); a
static "installing" placeholder page is served meanwhile. Allow a few minutes on first run.

Verify (poll until installed):

```bash
for i in $(seq 1 60); do
    curl -sf http://nextcloud.local/status.php | grep -q '"installed":true' && break
    [ "$i" = 60 ] && { echo "Nextcloud did not come up within 5 minutes"; exit 1; }
    sleep 5
done
./scripts/occ.sh nextcloud -- status
```

Expected: `status.php` JSON with `"installed":true`; `occ status` shows the master version.

If it fails:
- Placeholder page persists past ~5 minutes: `docker compose logs nextcloud database-mysql` and look for the DB
  wait loop or install errors.
- 502/503: `docker compose ps`; confirm `proxy` and `nextcloud` are both up.

## Stage 3: enable AppAPI and the notifications app

```bash
./scripts/occ.sh nextcloud -- app:enable app_api
docker compose exec --user "$(id -u):$(id -g)" -e COMPOSER_HOME=/tmp/composer \
    -w /var/www/html/apps-extra/notifications nextcloud composer install --no-dev
./scripts/occ.sh nextcloud -- app:enable notifications
```

Notes:
- `app:enable app_api` is a no-op if autoinstall already enabled it via the bootstrap argument.
- Autoinstall cannot enable `notifications`: the git checkout ships the `composer/` autoloader but no `vendor/`,
  and `app:enable` dies with `Failed opening required '.../notifications/composer/../vendor/autoload.php'`.
  `composer install` inside the container supplies it (the image has composer); `--user` keeps the bind-mounted
  files owned by you instead of root.
- Why the app matters here: AppAPI's notification endpoint (`POST /ocs/v2.php/apps/app_api/api/v1/notification`)
  hands the notification to the core notification manager, which delivers to whichever notifier apps are
  registered. With none, it delivers to nobody: the ExApp gets 200 and nothing is stored or shown (verified
  against `oc_notifications`), so a developer testing notifications on this instance would chase a bug that is
  not in their code.

Verify:

```bash
./scripts/occ.sh nextcloud -- app_api:daemon:list
./scripts/occ.sh nextcloud -- app:list | grep -E '^\s+- notifications:'
```

Expected: the first command runs (empty daemon list is fine at this point); the second prints
`- notifications: <version>` (it is listed under `Enabled:`; the grep would also match a `Disabled:` entry, so
confirm the section if in doubt).

If it fails:
- `app:enable notifications` fails with the `vendor/autoload.php` error: the `composer install` step was skipped
  or ran in the wrong directory; re-run it and enable again.
- `notifications` is missing from `apps-extra/`: the Stage 1 bootstrap ran without the argument (an existing
  checkout ignores arguments); clone it as described in Stage 1 and repeat this stage.

## Stage 4: add HaRP

Goal: the HaRP deploy-daemon container running next to Nextcloud.

First create the dev-only shared key as a file. HaRP reads it through `HP_SHARED_KEY_FILE` and Stage 6 reads
the same file, so the two cannot drift (a mismatch is the single most common install failure). A file rather
than an env value because `docker compose config` prints env values in clear, and two services in the tracked
compose file load the whole `.env` through `env_file`.

```bash
(umask 077; LC_ALL=C tr -dc A-Za-z0-9 </dev/urandom | head -c 32 > data/harp.key)   # any ASCII string works; LC_ALL=C keeps BSD tr (macOS) happy
echo data/harp.key >> .git/info/exclude
```

Create `docker-compose.override.yml` in the repository root (docker compose loads it automatically; it stays
untracked, optionally list it in `.git/info/exclude`):

```yaml
services:
  appapi-harp:
    image: ghcr.io/nextcloud/nextcloud-appapi-harp:release
    restart: unless-stopped
    environment:
      HP_SHARED_KEY_FILE: /run/secrets/harp.key
      NC_INSTANCE_URL: "http://nextcloud.local"
      HP_TRUSTED_PROXY_IPS: "192.168.21.0/24"   # DOCKER_SUBNET from .env
      HP_LOG_LEVEL: "info"
    volumes:
      - ${DOCKER_SOCKET-/var/run/docker.sock}:/var/run/docker.sock
      - harp-certs:/certs
      - ./data/harp.key:/run/secrets/harp.key:ro

volumes:
  harp-certs:
```

```bash
docker compose up -d appapi-harp
```

Notes:
- No `container_name`: compose names it `master-appapi-harp-1`, and the service name still provides the
  `appapi-harp` DNS alias on the compose network, which is what every later stage uses. A fixed name only
  invites collisions with older HaRP containers.
- HaRP exits at start when the key file is missing or empty, so a mistake fails at `up`, not at the first
  ExApp install. It reads the file when the container is created. To change the key, recreate the container
  (`docker compose up -d --force-recreate appapi-harp`): `up -d` alone does nothing because the compose
  config did not change, and a plain `restart` keeps the FRP configs HaRP generated with the old key inside
  the container, so HaRP stops with `frpc exited unexpectedly` and restarts in a loop (HaRP 0.4.5).
- FRP TLS stays on (the default): HaRP generates its certificates itself and AppAPI installs them into ExApp
  containers at deploy time; nothing to configure.
- The full `HP_*` reference lives in the HaRP README (Environment Variables section); nothing else needs tuning
  for a standard dev setup.

Verify:

```bash
docker compose ps appapi-harp
docker compose exec nextcloud curl -s -H "harp-shared-key: $(cat data/harp.key)" http://appapi-harp:8780/exapps/app_api/info
```

Expected: status `Up ... (healthy)` and a JSON body containing `"docker": true` (the `version` field shows the
HaRP version, e.g. `"0.4.3"`). Plain `GET /` on port 8780 returns 404 by design; never use it as a health probe.

## Stage 5: route /exapps/ through the proxy

Goal: browser traffic for ExApps (`http://nextcloud.local/exapps/...`) reaches HaRP.

Create `data/nginx/vhost.d/nextcloud.local` (the file name must exactly equal the virtual host, so it changes
with `DOMAIN_SUFFIX`; this per-vhost snippet mechanism is the documented nginx-proxy customization path):

```nginx
# Resolve HaRP at request time: with a literal proxy_pass hostname nginx refuses to start
# whenever the appapi-harp container is absent, and every vhost goes down with it.
set $harp_upstream http://appapi-harp:8780;
location /exapps/ {
    proxy_pass $harp_upstream;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 1800s;
}
```

Then restart the proxy service. A plain `nginx -s reload` is NOT enough the first time: nginx-proxy only adds
the `include vhost.d/<host>` line to its generated config if the file existed when the config was generated, and
a reload re-reads but never regenerates.

```bash
docker compose restart proxy
```

The `set` line is what keeps nginx up when HaRP is not running. nginx resolves a literal `proxy_pass` hostname
once at startup and refuses to start when it cannot (`[emerg] host not found in upstream "appapi-harp"`), which
takes every URL of every instance down, `status.php` included, until HaRP is back and the proxy restarts. A
variable is resolved per request through the `resolver 127.0.0.11` line nginx-proxy already generates, so HaRP
being down degrades to a 502 on `/exapps/` only, and recovery needs no proxy restart.

Verify:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://nextcloud.local/exapps/app_api/info
```

Expected: `401` (HaRP is reachable through the proxy and rejects the unsigned request; that is the pass signal).

If it fails:
- `404` with an **Apache** error page: the request fell through to Nextcloud, meaning the location block is not
  active: the vhost file name does not match the virtual host, or the proxy was not restarted after the file was
  created (check with `docker compose exec proxy grep vhost.d /etc/nginx/conf.d/default.conf`).
- `404` with an **nginx** error page: the vhost itself is wrong (rare; check `VIRTUAL_HOST`/`DOMAIN_SUFFIX`).
- `502`: HaRP container down (start it; no proxy restart needed), or wrong service name in the snippet.

## Stage 6: register the deploy daemons

Goal: two daemons: HaRP (docker-install) for production-like deploys, and manual-install for the fast loop.

```bash
# HaRP daemon (docker-install); --net is this compose project's network
./scripts/occ.sh nextcloud -- app_api:daemon:register \
    local-harp "HaRP (local)" docker-install http appapi-harp:8780 http://nextcloud.local \
    --net master_default \
    --harp --harp_frp_address appapi-harp:8782 --harp_shared_key "$(cat data/harp.key)" \
    --set-default

# Manual-install daemon (the ExApp process runs on your machine; its port comes from app registration)
./scripts/occ.sh nextcloud -- app_api:daemon:register \
    manual_dev "Manual (dev)" manual-install http host.docker.internal http://nextcloud.local
```

Notes:
- Protocol must be `http` for HaRP's port 8780 (8781 is the https frontend). Registering `https` against 8780
  fails later with "Connection refused for URI https://...".
- Re-registering an existing daemon name is a no-op. To change values, and in particular after changing the key,
  follow [Rotating the shared key](#rotating-the-shared-key): a plain `daemon:unregister` refuses while the
  daemon holds ExApps.
- Full flag reference: [operations.md](../../exapp-operations/references/operations.md#5-occ-app_apidaemonregister-reference).

Verify:

```bash
./scripts/occ.sh nextcloud -- app_api:daemon:list
```

Expected: both daemons listed, `local-harp` marked default.

## Stage 7: smoke test with the reference ExApp

Goal: prove the whole chain (AppAPI, HaRP, Docker, frpc, /exapps/ routing) with a real deploy before writing any
code. Uses [minimal_exapp](../../exapp-development/assets/minimal_exapp/), the reference ExApp bundled with the
exapp-development skill of this repository.

`<skills-repo>` below stands for the path of your copy of this skills repository (a git clone, or the installed
plugin's directory; any location works). The Makefile's occ calls go through `docker exec master-nextcloud-1`,
so the command works from any directory:

```bash
make -C <skills-repo>/skills/exapp-development/assets/minimal_exapp build register-docker
```

What this does: builds the image locally, maps its fictional registry `example.local` to `local` on the daemon
(so AppAPI skips the registry pull and uses your local image), registers the app, waits through heartbeat, init
(the app reports 25/50/75/100) and enable. The Makefile defaults match the daemon and container names above.

Verify:

```bash
./scripts/occ.sh nextcloud -- app_api:app:list
curl -s http://nextcloud.local/exapps/minimal_exapp/echo
curl -s http://nextcloud.local/exapps/minimal_exapp/nc-version
```

Expected: `minimal_exapp ... [enabled]`; `{"app_id": "minimal_exapp", ...}`; `{"nextcloud_version": "..."}` (the
second endpoint proves the ExApp can call Nextcloud OCS APIs).

This is the acceptance gate for the environment. Continue with
[exapp-development.md](../../exapp-development/references/exapp-development.md); clean up with
`make -C <skills-repo>/skills/exapp-development/assets/minimal_exapp unregister` when done.

## Stage 8 (optional): give the agent a browser

Goal: the agent can look at a rendered page instead of guessing from HTML.

This is optional for the environment and close to essential for UI work, on ExApps and PHP apps alike. An
agent without a browser verifies a page by fetching HTML and reasoning about it, which cannot see whether a
script executed, whether the Content Security Policy blocked it, or what a Vue component actually rendered.

Run a headless Chrome with remote debugging, and point an MCP browser server at it:

```bash
chromium --headless --no-sandbox --disable-gpu --disable-dev-shm-usage \
    --remote-debugging-address=0.0.0.0 --remote-debugging-port=9222 &
npm install -g chrome-devtools-mcp
```

Then declare the server for your agent. For Claude Code that is `.mcp.json` in the project root; other tools
have their own equivalent, and the server itself is tool-agnostic:

```json
{
    "mcpServers": {
        "chrome-devtools": {
            "type": "stdio",
            "command": "chrome-devtools-mcp",
            "args": ["--browserUrl", "http://localhost:9222"]
        }
    }
}
```

Attaching to an already-running browser (`--browserUrl`) is what suits a VM or container: the browser is a
long-lived service you can restart independently. The server can also launch its own browser if you leave
that flag out.

Verify:

```bash
curl -s http://localhost:9222/json/version
```

Expected: JSON naming the browser and protocol version. Then ask the agent to open a Nextcloud page and take
a snapshot; it should come back with the accessibility tree rather than raw HTML.

Why it earns its place: the snapshot reports each element's **role and accessible name**, which is exactly
what a Playwright locator needs. Opening the Nextcloud app menu returns

```
menu "Apps"
  menuitem "Minimal PHP App"
```

so the fact that app-menu entries are `menuitem` and not `link` is visible immediately, instead of costing a
debugging round against a correct app. The working pattern is: **explore with the browser, then freeze what
you learned into a test** ([php-app-ui-testing.md](../../nextcloud-php-app/references/php-app-ui-testing.md)).

Alternatives exist (`@playwright/mcp` is the other common one); this stage documents the setup verified here.

## Daily operation

- Start/stop: `docker compose up -d nextcloud appapi-harp` and `docker compose stop`. Never `down -v`.
- occ: `./scripts/occ.sh nextcloud -- <command>`; other instances: `./scripts/occ.sh stable34 -- <command>`.
- Logs: `docker compose logs -f nextcloud`; `docker compose logs -f appapi-harp`; ExApp logs:
  `docker logs -f nc_app_<appid>`.
- Update: `git -C workspace/server pull && git -C workspace/server submodule update --init 3rdparty`, pull
  apps-extra repos, `docker compose pull`, restart. Shallow-clone note: use `git fetch --depth=100` on shallow
  checkouts or a plain fetch downloads gigabytes of history.
- Stable-branch instances (test against NC34 etc.): prepare `workspace/stable34` per the upstream stable-versions
  doc, `docker compose up -d stable34`, then repeat Stages 3 to 6 against `stable34` with `stable34.local` names
  (including its own `data/nginx/vhost.d/stable34.local` snippet).

## Reset and recovery

| Situation | Action |
|---|---|
| Instance misbehaves after changes | `docker compose restart nextcloud`, then `./scripts/occ.sh nextcloud -- maintenance:repair` if needed |
| HaRP wedged | `docker compose restart appapi-harp` (safe; ExApp frpc clients retry and reconnect) |
| One service's config drifted | `docker compose up -d --force-recreate <service>` (containers are disposable; volumes hold state) |
| Failed ExApp install left a container behind | `./scripts/occ.sh nextcloud -- app_api:app:unregister <appid> --force` (`--force` because unregister first tries to disable the app, which fails when it is unreachable; add `--rm-data` to also drop its data volume) |
| Full reset (DESTROYS all instances' data) | Requires explicit human approval: `docker compose down -v`, then re-run from Stage 2 |
| Shared key changed | See [Rotating the shared key](#rotating-the-shared-key); the order matters |

### Rotating the shared key

Write the new value into `data/harp.key` and recreate HaRP with `docker compose up -d --force-recreate
appapi-harp` (not `restart`, see the Stage 4 note). From that moment AppAPI still signs with the old key and
HaRP answers 401 to it, `daemon:unregister`
refuses while the daemon holds ExApps ("contains N ExApps, please remove them first") and has no `--force`,
and the ExApps cannot be removed the normal way because that goes through HaRP. The order that works:

```bash
./scripts/occ.sh nextcloud -- app_api:app:unregister <appid> --force --silent   # per ExApp; drops the row without asking HaRP
./scripts/occ.sh nextcloud -- app_api:daemon:unregister local-harp
./scripts/occ.sh nextcloud -- app_api:daemon:register \
    local-harp "HaRP (local)" docker-install http appapi-harp:8780 http://nextcloud.local \
    --net master_default \
    --harp --harp_frp_address appapi-harp:8782 --harp_shared_key "$(cat data/harp.key)" \
    --set-default
```

Then redeploy the ExApps (Stage 7's `make register-docker` for the reference app); AppAPI replaces the orphaned
containers itself. Registry mappings live in the daemon config and go with it: Stage 7's target re-adds the
reference app's mapping by itself, anything you added by hand with `app_api:daemon:registry:add` must be added
again.

## Troubleshooting (symptom first)

| Symptom | Likely cause and fix |
|---|---|
| Placeholder "installing" page forever | DB not ready or install failed: `docker compose logs nextcloud database-mysql` |
| `nextcloud.local` does not resolve | `/etc/hosts` not updated (sudo) or dnsmasq wildcard missing |
| `daemon:register` cannot connect | Wrong `--net` (must be this project's network, default `master_default`), HaRP down, or host typo |
| Deploy fails instantly: "Connection refused for URI https://..." | Daemon registered with protocol `https` against HaRP's http port 8780; unregister and re-register with `http` |
| Deploy fails at pull: `.../images/create ... 500` | Image exists only locally and the daemon has no `registry ... to local` mapping; see [exapp-development.md](../../exapp-development/references/exapp-development.md) |
| Install hangs minutes, then "heartbeat check failed"; app left `[disabled]`, container running | Shared-key mismatch between daemon and `HP_SHARED_KEY` (failure number one), or the image lacks the frpc/start.sh HaRP adaptation; check `docker logs nc_app_<appid>` for "start proxy success" |
| `/exapps/` returns an Apache 404 page | Fell through to Nextcloud: vhost.d file name wrong, or the proxy was never restarted after the file was created (Stage 5) |
| `/exapps/<app>/<declared-route>` returns 404 | Route not declared in the app's manifest, or the app sits on a non-HaRP daemon (manual apps are served at `/index.php/apps/app_api/proxy/<appid>/...`) |
| `/exapps/` returns 502 | HaRP container down, or a browser request to an infrastructure route (`/heartbeat`, `/init`, `/enabled`), which is blocked by design |
| Every URL refuses the connection, `status.php` too, while Nextcloud logs look fine | nginx did not start: the `/exapps/` snippet names `appapi-harp` in a literal `proxy_pass` and the container is absent (`docker compose logs proxy` shows `[emerg] host not found in upstream`). Use the Stage 5 `set $harp_upstream` form; until then, start HaRP and the proxy recovers within seconds |
| HaRP restarts in a loop after a key change; its log ends with `frpc exited unexpectedly` | A plain `restart` kept the FRP configs generated with the old key. `docker compose up -d --force-recreate appapi-harp` |
| Ports 80/443 busy on the host | Another web server; stop it or change the bind/port settings in `.env` |
| An ExApp sends a notification, AppAPI answers 200, nothing ever appears | The `notifications` app is not enabled: git checkouts do not include it and the app store has no build for a development version; do Stage 3 |

More production-shaped symptoms:
[operations.md troubleshooting](../../exapp-operations/references/operations.md#10-troubleshooting-symptom-first).

## Related

- [operations.md](../../exapp-operations/references/operations.md):
  [Quickstart (production install)](../../exapp-operations/references/operations.md#2-quickstart-zero-to-a-working-exapp),
  [`daemon:register` reference](../../exapp-operations/references/operations.md#5-occ-app_apidaemonregister-reference),
  [ExApp lifecycle](../../exapp-operations/references/operations.md#6-exapp-lifecycle-occ).
- [exapp-development.md](../../exapp-development/references/exapp-development.md): write and iterate on an ExApp
  in any language.
- [exapp-ai-maintenance.md](../../exapp-maintenance/references/exapp-ai-maintenance.md): fix or extend an
  installed ExApp.
- nextcloud-docker-dev documentation: https://nextcloud.github.io/nextcloud-docker-dev/
- HaRP README (env vars, reverse-proxy examples, adapting ExApps): https://github.com/nextcloud/HaRP
