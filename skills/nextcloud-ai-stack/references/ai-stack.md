<!--
  - SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Installing and running the AI stack

A detailed runbook, part of the [nextcloud-ai-stack](../SKILL.md) skill.

This page gets AI features working on a Nextcloud and keeps them working. It assumes admin shell access. For
which feature is provided by which app, the authoritative matrix is the
[AI overview in the admin manual](https://docs.nextcloud.com/server/latest/admin_manual/ai/overview.html);
this runbook covers the part the manual does not: the order to do things in, the commands that prove each
step, and the operational facts that decide your hardware.

Last verified against: Nextcloud master (35), AppAPI 35.0.0-dev.1, HaRP 0.4.3, llm2 2.8.0 and integration_openai, on 2026-08-11.

Throughout, `occ` means the Nextcloud server console; on a Docker install run it as
`docker exec -u www-data <nextcloud-container> php occ <command>`
([operations.md](../../exapp-operations/references/operations.md) preamble has the other variants).

## Decide first: local models or an external API

Everything else follows from this choice.

| | Local providers (ExApps) | External API integrations |
|---|---|---|
| Examples | llm2, translate2, stt_whisper2, text2image_stablediffusion2, context_chat_backend | integration_openai, integration_watsonx, integration_deepl |
| Data | stays on your hardware | leaves your instance |
| Needs | AppAPI + a deploy daemon, RAM or VRAM, tens of GB of disk | an API key |
| Install shape | `occ app_api:app:register ...` | ordinary app plus credentials in admin settings |
| Speed | GPU is fast, CPU is slow but works | fast |

Mixing is normal: local text generation, an external translator, no image generation at all. Both kinds
register as Task Processing providers, so everything below applies to both.

For OpenAI or OpenAI-compatible APIs, or whenever you lack GPU/disk for stock llm2's multi-GB init, follow
[openai-integration.md](openai-integration.md) instead of registering llm2. You will still need to install
assistant first (step 2 below).

## Install order

Doing these out of order is the usual reason an admin sees an empty Assistant.

**1. AppAPI and a daemon (local providers only).** Register the daemon **with the compute device you intend
to use**, because that is a property of the daemon, not of the app:

```bash
occ app_api:daemon:register ... --compute_device cuda   # or rocm, default cpu
```

Full daemon setup: [operations.md Quickstart](../../exapp-operations/references/operations.md#2-quickstart-zero-to-a-working-exapp).
Changing the compute device later means unregistering the daemon and reinstalling the apps on it.

**2. The frontend.** Prefer the store when it matches your server:

```bash
occ app:install assistant
occ app:enable assistant
```

On **Nextcloud master / a version ahead of the app's `max-version`**, `app:install` fails with
"not compatible with this version of the server" even though the app would run. Install from source into
`apps-extra` (nextcloud-docker-dev layout) and bump the dependency range:

```bash
git clone --depth 1 https://github.com/nextcloud/assistant.git workspace/server/apps-extra/assistant
# bump max-version in appinfo/info.xml to match the server major (e.g. 36)
sudo chown -R 33:33 workspace/server/apps-extra/assistant   # www-data in the PHP container
docker compose exec -u www-data nextcloud \
  bash -lc 'cd /var/www/html/apps-extra/assistant && composer install --no-dev -n'
# Frontend assets are not shipped in git — always build them (Node must satisfy package.json engines;
# on nextcloud-docker-dev the PHP container usually has nvm under /root/.nvm):
docker compose exec nextcloud bash -lc \
  '. /root/.nvm/nvm.sh 2>/dev/null; cd /var/www/html/apps-extra/assistant && npm ci && npm run build && chown -R www-data:www-data .'
occ app:enable assistant
```

Run `npm ci && npm run build` for **every** PHP AI app installed from source (Assistant,
`integration_openai`, …). Without the Vite output under `js/` / `css/`, the admin and user UI is blank even
when providers work.

Assistant only draws the UI; on its own it can do nothing.

**3. The providers you actually want.** One app per capability:

```bash
occ app_api:app:register llm2 --wait-finish        # text generation
occ app_api:app:register translate2 --wait-finish  # machine translation
occ app_api:app:register stt_whisper2 --wait-finish # speech to text
```

If that fails with `Failed to get app info for '<id>' from the Appstore`, the app has no release for your
Nextcloud version in the store feed. Check with:

```bash
curl -s https://apps.nextcloud.com/api/v1/platform/<nc-version>/apps.json | grep -c '"id": "<appid>"'
```

A `0` means the store cannot serve it (this is the normal situation on the Nextcloud 35+ dev line today).
Install from a manifest instead, using the app's own `appinfo/info.xml` from its repository.

If the manifest's `<nextcloud max-version="…"/>` is behind your server major, bump it in a **local copy** of
the XML before registering (same version-skew pattern as Assistant). Do not push that bump unless you own the
upstream release.

`--info-xml` must be a path the **Nextcloud PHP process** can read. On nextcloud-docker-dev that means a path
*inside* the Nextcloud container, not only on the host:

```bash
# host → container, then register with the in-container path
docker cp /path/on/host/info.xml master-nextcloud-1:/tmp/llm2-info.xml
occ app_api:app:register llm2 <daemon> --info-xml /tmp/llm2-info.xml --wait-finish
```

A host-only absolute path fails even when the file exists on the machine running `occ.sh`.

**4. Wait for init.** This is where the surprise lives, see below. On constrained hosts (CPU-only, limited
RAM/disk, Cloud Agents), skip llm2 and use [openai-integration.md](openai-integration.md) instead.

**5. Pick providers per task type** in **Administration settings > Artificial intelligence** when more than
one app can serve a task type.

## Init: the step that looks broken and is not

A provider ExApp downloads its models during `/init`, which happens after the container is running. Two
things follow, both verified:

- `occ app_api:app:list` shows the app as `[enabled]` while init is still in progress. It is not a readiness
  signal. Observed: llm2 listed as `[enabled]` while its init progress was 15 percent and a 4 GB model file
  was still being written.
- **The app registers its Task Processing providers only after init finishes.** The mechanism is the
  lifecycle itself: AppAPI calls the app's `/enabled` endpoint once init reports 100, and that handler is
  where a provider app registers its providers (llm2 builds one per available model there). Until then the
  task types it serves do not exist for Nextcloud, and the Assistant shows nothing.

Watch the real progress instead:

```bash
docker logs -f nc_app_<appid>                       # what it is downloading right now
docker exec nc_app_<appid> ls -l /nc_app_<appid>_data   # model files growing; .tmp means in flight
```

The admin UI (**Administration settings > AppAPI**) shows the same progress as a percentage per app. Budget
for it properly: an app usually fetches **several** models one after another, and the percentage steps once
per model rather than smoothly. Measured on llm2 2.8.0 over a fast link: the container was up and healthy
about 90 seconds after registration, the first model (5.2 GB) finished roughly 15 minutes later, and init
stood at 25 percent while the second model started. Plan in tens of minutes and tens of GB, not in seconds —
or skip local llm2 and use [openai-integration.md](openai-integration.md).

## Verify: the acceptance recipe

Three commands, in this order. Stop at the first one that fails.

**1. Does a provider exist for the task type?** This endpoint returns **only** task types that currently have
a provider, which makes it the ground truth:

```bash
curl -s -u <admin>:<pass> -H 'OCS-APIRequest: true' \
    '<nextcloud-url>/ocs/v2.php/taskprocessing/tasktypes?format=json'
```

Server-defined types are prefixed `core:` (for example `core:text2text`), app-defined ones carry the app id.
An empty or short list means no provider finished registering.

One caveat, verified in the server source and observed live: this list is held in the distributed cache with a
**60 second TTL** (`OC\TaskProcessing\Manager`), so a provider that has just finished init can take up to a
minute to show up. Re-query before concluding that registration failed.

**2. Schedule a real task.** `input` is the task type's input schema, `appId` is any identifier you choose:

```bash
curl -s -u <admin>:<pass> -H 'OCS-APIRequest: true' -H 'Content-Type: application/json' \
    -X POST '<nextcloud-url>/ocs/v2.php/taskprocessing/schedule?format=json' \
    -d '{"type": "core:text2text", "appId": "acceptance-check", "customId": "verify",
         "input": {"input": "Say hello in one word."}}'
```

**3. Follow it to completion:**

```bash
occ taskprocessing:task:get <id>     # status: scheduled -> running -> successful
occ taskprocessing:task:list --appId acceptance-check
occ taskprocessing:task:stats
```

A task that stays `scheduled` is a scheduling problem, not a model problem; go to
[ai-troubleshooting.md](ai-troubleshooting.md).

## Model storage, disk and memory

- Models live in the ExApp's persistent volume, `nc_app_<appid>_data`, mounted at `/nc_app_<appid>_data`.
  Inspect size with `docker system df -v` or `du -sh` on the volume path.
- The volume survives updates and re-registration. `occ app_api:app:unregister <appid> --rm-data` deletes it,
  and with it every downloaded model. Treat `--rm-data` on an AI app as a destructive action needing explicit
  approval.
- Plan tens of GB for local providers: a single quantised text model is several GB, and an instance running
  text, speech and image generation multiplies that.
- On CPU, generation is memory-bound and slow; this is expected, not a misconfiguration. Give the host enough
  RAM to hold the model plus the working set, or move to a GPU daemon.

## GPU

- The daemon carries the device (`--compute_device cuda|rocm`); AppAPI injects `COMPUTE_DEVICE` plus the
  NVIDIA runtime variables into the container.
- The app must ship a matching image variant; llm2 for instance builds from `Dockerfile.cpu`,
  `Dockerfile.cuda` and `Dockerfile.rocm`.
- Host prerequisites are the usual ones for containers with GPUs (driver plus container toolkit). Verify from
  inside the running app container rather than trusting the host: `docker exec nc_app_<appid> nvidia-smi`.
- A GPU daemon on a separate machine is a normal topology:
  [remote-daemon.md](../../exapp-operations/references/remote-daemon.md).

## Day 2

- **Cron must run.** Task Processing dispatches through background jobs. If cron is broken, tasks queue and
  nothing else in the stack matters.
- **Throughput**: `occ taskprocessing:worker` runs a dedicated worker for **synchronous** providers, with
  `--taskTypes` to restrict it and `--once` to process a single task. Run several for parallelism. It does
  nothing for provider ExApps: those claim their own tasks, so a worker started against a task type served by
  an ExApp exits immediately and leaves the task where it was (verified).
- **Housekeeping**: `occ taskprocessing:task:cleanup [maxAgeSeconds]` removes old tasks. Task results can be
  large, so schedule it.
- **Turning a capability off**: `occ taskprocessing:task-type:set-enabled <task-type-id> <0|1>` rather than
  uninstalling the provider.
- **Updating a provider**: ordinary ExApp update; the data volume with the models is preserved
  ([exapp-ai-maintenance.md](../../exapp-maintenance/references/exapp-ai-maintenance.md)). Patching a
  provider's behaviour, for example adding a model, is that runbook's worked example.

## Related

- [openai-integration.md](openai-integration.md): install and configure `integration_openai`.
- [ai-troubleshooting.md](ai-troubleshooting.md): symptom-first diagnosis.
- [operations.md](../../exapp-operations/references/operations.md): daemon setup and the ExApp lifecycle.
- [harp-operations.md](../../harp-operations/references/harp-operations.md): when the provider apps themselves
  are unreachable.
- Admin manual: [AI overview](https://docs.nextcloud.com/server/latest/admin_manual/ai/overview.html),
  [insight and debugging](https://docs.nextcloud.com/server/latest/admin_manual/ai/insight_and_debugging.html).
