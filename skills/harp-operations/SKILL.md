---
name: harp-operations
description: >-
  Runs and debugs HaRP itself, the reverse proxy that serves as AppAPI's deploy daemon: deploying the
  container, the environment variables worth setting, health probes and what their answers mean, reading
  HAProxy and agent logs, client IP handling behind another reverse proxy, certificates and the FRP tunnel,
  upgrades and version floors, and load-related failures. Use when HaRP returns 401, 403, 404, 502 or 503,
  when ExApps are unreachable although their containers run, or when installing, tuning or upgrading HaRP.
license: AGPL-3.0-or-later
compatibility: >-
  HaRP 0.4.x with AppAPI on Nextcloud 33, 34 or 35; needs shell access to the host running the HaRP
  container. Last verified with HaRP 0.4.3, AppAPI 35.0.0-dev.1, Nextcloud master (35).
---

# HaRP operations

HaRP is the service AppAPI drives to deploy and reach External Apps. It is three things in one container: the
HTTP frontend that carries all ExApp traffic, the FRP tunnel server that ExApp containers dial back into, and
the control path AppAPI uses to manage Docker or Kubernetes. Most "my ExApp is broken" reports are really one
of those three planes failing, and each fails with a different signature.

This skill is HaRP itself. Registering daemons, installing ExApps and the occ surface are
[exapp-operations](../exapp-operations/SKILL.md).

## How to work

1. Read [references/harp-operations.md](references/harp-operations.md): the planes, the probes, the
   configuration that matters, log decoding, upgrades, certificates and symptom-first troubleshooting.
2. Collect the state before theorising: [assets/harp-triage.sh](assets/harp-triage.sh) prints container
   health, the reported version, ports, the probe results and recent errors, with secrets redacted.
3. Fix the daemon registration or the ExApp instead if the fault is on that side:
   [exapp-operations](../exapp-operations/SKILL.md).

## Facts that save hours

- Read the status code first, it names the plane: `401` shared key, `403` route access level, `404` unknown
  route or an app not on this daemon, `405` a method the route's `verb` does not list (HaRP 0.4.8+), `502` a
  request HaRP dropped (an infrastructure route, a dot segment or a banned client IP; a timeout across networks)
  or a proxy rule pointing nowhere, `503` the agent under load.
- `GET /` on the HTTP frontend answers `404` by design. Never use it as a health probe; use
  `/exapps/app_api/info` with the `harp-shared-key` header.
- One HaRP serves one Nextcloud. `NC_INSTANCE_URL` is a single value and every request is authorised against
  that instance.
- HaRP must be reachable at `<nextcloud_url>/exapps/`: AppAPI talks to ExApps through the public URL, so a
  missing reverse-proxy rule blocks installs, not just browsers.
- Regenerating the FRP certificates invalidates every deployed ExApp: they embed them at install time and
  must be reinstalled, not restarted.
- `:release` is the newest release, `:latest` and `:main` are built from `main`. Pin `:vX.Y.Z` if you need a
  known version.

## Files

- [references/harp-operations.md](references/harp-operations.md): the runbook.
- [assets/harp-triage.sh](assets/harp-triage.sh): read-only diagnostics collector.
- Daemon registration, ExApp lifecycle and the occ surface:
  [exapp-operations](../exapp-operations/SKILL.md). ExApps on a separate host or in Kubernetes:
  [remote-daemon.md](../exapp-operations/references/remote-daemon.md),
  [kubernetes.md](../exapp-operations/references/kubernetes.md).
