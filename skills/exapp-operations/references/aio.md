<!--
  - SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->

# ExApps on Nextcloud AIO

A short runbook, part of the [exapp-operations](../SKILL.md) skill. Read this when the Nextcloud instance is
**AIO (all-in-one)**. AIO manages the deploy daemon for you: most of the manual setup in the `operations.md`
Quickstart does not apply, and fighting the managed daemon is the main failure mode.

Last verified against: Nextcloud master (35), AppAPI 35.0.0-dev.1, HaRP 0.4.3, on 2026-08-10.

## What AIO automates

AppAPI detects AIO via the `THIS_IS_AIO` env var and auto-registers the deploy daemon during app
install/upgrade (`lib/DeployActions/AIODockerActions.php`, `lib/Migration/DataInitializationStep.php`):

- With AIO's **HaRP container** enabled (`HARP_ENABLED` + `HP_SHARED_KEY` set by AIO), AppAPI registers
  **`harp_aio`** (`nextcloud-aio-harp:8780`, network `nextcloud-aio`, HaRP direct-connect mode) and makes it
  the default. Exists on NC33+.
- With the legacy **Docker Socket Proxy** container, NC33 to NC35 register **`docker_aio`**
  (`nextcloud-aio-docker-socket-proxy:2375`), deprecated since NC34. NC36 registers nothing for it; the
  app_api install or upgrade only warns
  `Docker Socket Proxy is enabled in AIO, but AppAPI no longer supports it. Use HaRP instead.`
- On NC33 to NC35, if both are enabled, `harp_aio` wins the default. Registration is idempotent and AppAPI
  never removes a daemon it registered: a `docker_aio` from an earlier version stays behind, and NC36 no
  longer manages it. Move its ExApps to `harp_aio` with the steps below.
- AIO's own web server container routes `/exapps/` to HaRP internally, so the `operations.md` Quickstart
  Step 4 proxy rule is only needed on a reverse proxy **in front of** AIO, if you run one.

## What the admin does

1. In the AIO interface, enable the **HaRP community container** (or add `harp` to the
   `AIO_COMMUNITY_CONTAINERS` env var of the mastercontainer), then restart the AIO containers so the env
   reaches the Nextcloud container.
2. That is all for the daemon: check **Settings --> Administration --> AppAPI** shows the `AIO HaRP` daemon,
   and use **Check connection** / **Test deploy**.
3. Install ExApps normally (UI store, or `occ app_api:app:register <appid> --wait-finish`; on AIO run occ as
   `docker exec -u www-data nextcloud-aio-nextcloud php occ ...`).

## What NOT to do on AIO

- Do **not** run your own HaRP container or hand-register another HaRP daemon; `harp_aio` is AIO-managed
  (a second daemon competes with it, and re-registering the same name is a no-op).
- Do **not** edit `harp_aio`'s host or shared key by hand; AIO owns `HP_SHARED_KEY` and a mismatch breaks
  Nextcloud-to-HaRP auth.
- Do not re-default `docker_aio` (deprecated on NC34 and NC35, unmanaged on NC36); move its ExApps to
  `harp_aio` and unregister it.

## Moving ExApps from `docker_aio` to `harp_aio`

Needed on NC36, where `docker_aio` is no longer managed; the same steps work on NC33 to NC35. Both daemons use
AIO's Docker Engine, so a reinstalled ExApp reuses its data volume. Details and the NC36 behavior:
[operations.md](operations.md#migrating-exapps-from-docker-socket-proxy-to-harp).

1. Enable the HaRP community container (What the admin does, step 1). A newly registered `harp_aio` becomes
   the default; if `harp_aio` existed before, set it as default in **Settings --> Administration --> AppAPI**.

   ```bash
   occ app_api:daemon:list   # "*" in Def on the harp_aio row
   ```

2. Move each ExApp, never with `--rm-data` (it deletes the data volume):

   ```bash
   occ app_api:app:unregister <appid>
   occ app_api:app:register <appid> harp_aio --wait-finish
   occ app_api:app:list      # <appid> (<name>): <version> [enabled]
   ```

3. Remove `docker_aio` with occ (the admin UI removal also uninstalls any ExApp still on it, and on NC33 to
   NC35 deletes their data volumes), then disable AIO's Docker Socket Proxy container:

   ```bash
   occ app_api:daemon:unregister docker_aio   # refused while ExApps still use it
   occ app_api:daemon:list                    # docker_aio is gone
   ```

## Troubleshooting

- **"AppAPI default deploy daemon is not set"** on AIO: the HaRP community container is not enabled or not
  running, so `HARP_ENABLED`/`HP_SHARED_KEY` never reached the Nextcloud container and auto-registration
  skipped itself. Enable the container, restart AIO, and re-check.
- **HaRP enabled but no `harp_aio` daemon**: the Nextcloud container was not recreated after enabling HaRP,
  so the env vars are missing inside it. Restart/recreate the AIO containers; the (idempotent) registration
  re-runs on the next app_api install/upgrade repair step.
- **NC36 setup check "AppAPI Docker Socket Proxy daemons" names `docker_aio`, or no default daemon while
  only AIO's Docker Socket Proxy is enabled**: NC36 does not use it. Enable the HaRP community container and
  move the ExApps (above).
- **ExApp installs fine but its page is blank / WebSocket fails, only behind your own proxy**: the reverse
  proxy in front of AIO does not forward `/exapps/` (with WebSocket upgrade). The internal checks pass anyway;
  add the rule from `operations.md` Quickstart Step 4 pointing at AIO.

For everything else (lifecycle, troubleshooting, version notes) see [operations.md](operations.md).
