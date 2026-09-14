---
name: nextcloud-dev-setup
description: >-
  Sets up a complete local Nextcloud development environment with Docker Compose using nextcloud-docker-dev:
  Nextcloud from source, the AppAPI app, a HaRP deploy daemon, /exapps/ browser routing, and a smoke-tested
  ExApp deploy as the acceptance gate. Use when asked to create or bootstrap a Nextcloud dev instance, prepare
  an environment for ExApp development, or repair a broken nextcloud-docker-dev setup.
license: AGPL-3.0-or-later
compatibility: >-
  Linux or macOS with Docker Engine, docker compose v2, git, curl, make and sudo; 4+ vCPUs, 8+ GB RAM, 40+ GB
  disk recommended. macOS runs the runbook unmodified; see references/macos.md. Last verified with Nextcloud
  master (36), AppAPI 36.0.0-dev.0, HaRP 0.4.5.
---

# Nextcloud development environment setup

Builds a disposable local Nextcloud development environment on
[nextcloud-docker-dev](https://github.com/nextcloud/nextcloud-docker-dev), then layers everything ExApp
development needs on top: AppAPI enabled, a HaRP deploy daemon (production-like docker-install) plus a
manual-install daemon (fast loop), `/exapps/` routed through the bundled nginx proxy, and a real reference
ExApp deployed as proof the whole chain works.

## How to execute

Read [references/dev-environment.md](references/dev-environment.md) in full, then execute its stages 1 to 7 in
order. Every stage ends with a Verify block; do not continue past a failed Verify (each has a matching "If it
fails" entry). Stage 7, deploying the bundled
[minimal_exapp](../exapp-development/assets/minimal_exapp/) through HaRP, is the acceptance gate: when its
endpoints answer over `http://nextcloud.local/exapps/...`, the environment is done.

## Rules that prevent the common disasters

- Never run `docker compose down -v` and never delete `workspace/` or named volumes without explicit human
  approval; they hold every instance's data.
- Never edit tracked files of the nextcloud-docker-dev checkout. The whole AppAPI overlay lives in untracked
  files (`.env`, `docker-compose.override.yml`, `data/nginx/vhost.d/`, `data/harp.key`).
- The HaRP shared key lives in one untracked file, `data/harp.key`: HaRP mounts it as `HP_SHARED_KEY_FILE` and
  `daemon:register --harp_shared_key "$(cat data/harp.key)"` reads the same file. A mismatch is the number one
  install failure, and changing the key has a fixed order (Rotating the shared key in the runbook).
- Keep the `set $harp_upstream` line in the `/exapps/` snippet: a literal `proxy_pass` hostname stops nginx
  from starting whenever HaRP is absent, which takes every instance down, `status.php` included.
- After creating a new `data/nginx/vhost.d/<host>` file, `docker compose restart proxy`; a plain nginx reload
  never picks up a file that did not exist when the config was generated.
- Kill development processes by exact PID only, and use dev-only secrets you never reuse elsewhere.

## Files

- [references/dev-environment.md](references/dev-environment.md): the full staged runbook (bring-up, daemons,
  smoke test, an optional browser for the agent, daily operation, reset and recovery, troubleshooting).
- [references/macos.md](references/macos.md): read first on a Mac. Verified end to end on Apple Silicon: what
  genuinely differs, which ExApp images have arm64 builds, the Docker socket, and the adjustments you do *not*
  need.
- [assets/macos-preflight.sh](assets/macos-preflight.sh): checks a Mac and prints the `.env` lines to use.
- Next steps: build your own ExApp with the [exapp-development](../exapp-development/SKILL.md) skill; operate
  a real instance with [exapp-operations](../exapp-operations/SKILL.md).
