---
name: exapp-operations
description: >-
  Installs, operates and troubleshoots Nextcloud AppAPI and External Apps (ExApps) with occ: set up the HaRP
  deploy daemon on Docker, Kubernetes, a remote GPU host or Nextcloud AIO, route /exapps/ on the reverse
  proxy, install, enable, disable, update and remove ExApps, manage daemon registries and ExApp config, move
  ExApps from Docker Socket Proxy to HaRP, and diagnose daemon or routing failures symptom-first. Use for
  AppAPI admin questions and day-2 ExApp operations on development or production instances.
license: AGPL-3.0-or-later
compatibility: >-
  Nextcloud 33, 34 and 35 (36 where noted) with admin (occ) access; Kubernetes daemons need NC34+. Last
  verified with Nextcloud master (35), AppAPI 35.0.0-dev.1, HaRP 0.4.3.
---

# AppAPI and ExApp operations

AppAPI is the Nextcloud component that runs External Apps: services living outside the PHP process, usually
as Docker containers, deployed and managed through a deploy daemon (HaRP is the recommended one). This skill
is the operator's view: getting a daemon working on a real instance, the full occ command surface, and what
to check when something breaks.

## How to work

Start in [references/operations.md](references/operations.md); it is the hub:

- New instance, no daemon yet: follow its **Quickstart** (section 2), an eight-step golden path from
  `occ app:enable app_api` to a running ExApp.
- Choosing or registering a daemon: sections 3 to 5 (deploy types, topologies, the full
  `daemon:register` flag table). Moving ExApps off Docker Socket Proxy, which NC36 refuses: section 3.
- Managing installed ExApps: section 6 (lifecycle commands and their semantics) and section 7 (config,
  registries, certificates, maintenance mode).
- Something is broken: section 10 (symptom-first troubleshooting) plus section 11 (which capability exists in
  which Nextcloud version).

Special topologies have their own runbooks, same directory:

- [references/kubernetes.md](references/kubernetes.md): ExApps in a Kubernetes cluster (NC34+); HaRP runs
  off-cluster, expose types are the core decision.
- [references/remote-daemon.md](references/remote-daemon.md): ExApp containers on a different machine than
  Nextcloud, for example a GPU server.
- [references/aio.md](references/aio.md): Nextcloud AIO, where the daemon is auto-managed and the main
  failure mode is fighting it.

## Facts that save hours

- The number one failure is a shared-key mismatch: `--harp_shared_key` must be byte-identical to HaRP's
  `HP_SHARED_KEY`.
- `daemon:register` is a silent no-op when a daemon with that name exists; `daemon:unregister` first to change
  anything.
- NC36 refuses Docker Socket Proxy (a `docker-install` daemon without `--harp`) and no longer manages existing
  ones. Move their ExApps by unregistering without `--rm-data` and registering again on HaRP on the same
  Docker Engine, which reuses the data volume.
- Green daemon checks do not prove the ExApp path. Without a working `/exapps/` reverse-proxy rule (with
  WebSocket upgrade) no ExApp can even be installed on a HaRP daemon: AppAPI polls the heartbeat at
  `<nextcloud_url>/exapps/<appid>`, so the install fails while `DaemonCheck` stays green.
- Never paste real secrets into shared files or commands you log; placeholders in the runbooks are in angle
  brackets.
- Destructive flags need explicit human approval: `app:unregister --rm-data` deletes the app's data volume.

## Files

- [references/operations.md](references/operations.md): concepts, Quickstart, daemon and lifecycle command
  references, operating notes, app store, runtime contract, troubleshooting, version notes.
- [references/kubernetes.md](references/kubernetes.md), [references/remote-daemon.md](references/remote-daemon.md),
  [references/aio.md](references/aio.md): topology runbooks.
- The HaRP service itself (probes, logs, upgrades, load):
  [harp-operations](../harp-operations/SKILL.md). AI features on top of AppAPI:
  [nextcloud-ai-stack](../nextcloud-ai-stack/SKILL.md).
- Building the app side of the contract: [exapp-development](../exapp-development/SKILL.md). Changing an
  installed app: [exapp-maintenance](../exapp-maintenance/SKILL.md). Local dev environment:
  [nextcloud-dev-setup](../nextcloud-dev-setup/SKILL.md).
