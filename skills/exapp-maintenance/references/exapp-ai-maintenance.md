<!--
  - SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Fix or extend an installed ExApp (with an AI agent)

A detailed runbook, part of the [exapp-maintenance](../SKILL.md) skill.

ExApps are open source and run from Docker images, which means any installed ExApp can be changed locally: when
something is broken or missing, you (or the AI coding agent working for you) can get the source, make the change,
rebuild the image, and make your Nextcloud run the rebuilt one, without waiting for a release. This runbook is
that loop. It is written for an AI agent executing it on a user's behalf; the user's part is one sentence, like
"add EuroLLM support to llm2" or "llm2 never reports progress, fix it".

Works against any docker-install HaRP daemon you control, including the development environment from
[dev-environment.md](../../nextcloud-dev-setup/references/dev-environment.md). Not covered: Kubernetes daemons
(the build-and-tag steps below need the Docker daemon host; a Kubernetes daemon takes a rebuilt image through a
registry mapping instead, see [kubernetes.md](../../exapp-operations/references/kubernetes.md)) and manual-install
apps (just edit and restart the process; see
[exapp-development.md](../../exapp-development/references/exapp-development.md)).

Last verified against: Nextcloud master (35), AppAPI 35.0.0-dev.1, HaRP 0.4.3, on 2026-08-11.

Throughout, `occ` means the Nextcloud server console; on a Docker install run it as
`docker exec -u www-data <nextcloud-container> php occ <command>`, on snap `nextcloud.occ`, on bare-metal
`sudo -u www-data php occ` (full notes:
[operations.md](../../exapp-operations/references/operations.md) preamble).

## Ground rules for the agent

- Never use `--rm-data`; the app's data volume and configuration are the user's.
- Prefer the smallest change that solves the stated problem; follow the upstream project's conventions.
- Registry mappings are daemon-wide: adding `--registry-from ghcr.io --registry-to local` stops image pulls for
  EVERY app from that registry on that daemon. Acceptable on a dev daemon; on a shared instance, remove the
  mapping right after the update (step 6) or plan deliberately.
- The user sees results, not diffs: finish with the verification step and offer to submit the change upstream.

## The runbook

### 1. Identify what is running

```bash
occ app_api:app:list                 # appid, version, enabled state
occ app_api:daemon:list              # which daemon; must be a docker-install HaRP daemon for this runbook
docker ps --filter name=nc_app_<appid>
```

### 2. Get the matching source

The app store page (`https://apps.nextcloud.com/apps/<appid>`), the catalog in
[known-exapps.md](../../exapp-development/references/known-exapps.md), and `<repository>` in the app's
info.xml point at the source repository. Clone it and check out the tag matching the INSTALLED version from
step 1 (fix what is running, not the moving main branch):

```bash
git clone <repo> && cd <repo> && git checkout v<installed-version>    # tag scheme varies by project
```

### 3. Diagnose or design

- Runtime evidence: `docker logs nc_app_<appid>`, the Nextcloud log, the app's own endpoints.
- For a missing capability, find where the equivalent existing capability lives and mirror it (worked example
  below: new model support = one list entry + one config block).

### 4. Make the change and bump the version

Edit the source, then bump `<version>` in `appinfo/info.xml`. Use a fourth version segment for local builds,
for example `2.8.0.1` on top of `2.8.0`: it sorts above the installed release but below upstream's next patch
(`2.8.1`), so a future store update still takes over cleanly. The bump is not cosmetic: `app_api:app:update`
compares versions and is a verified no-op when they are equal, so an unbumped rebuild never reaches the
container.

### 5. Rebuild the image under its original name

Build with the exact coordinates from info.xml (`<registry>/<image>:<image-tag>`), on the machine whose Docker
daemon the HaRP daemon uses:

```bash
docker build -f <Dockerfile variant> -t <registry>/<image>:<new-tag> .
```

(Also update `<image-tag>` in info.xml to the new tag; keeping it equal to `<version>` is the common
convention.) Pick the Dockerfile variant matching the daemon's compute device, shown by
`occ app_api:daemon:list`: `cpu` daemons want the CPU build (llm2: `Dockerfile.cpu`), `cuda`/`rocm` daemons
the GPU build, per the project's own build docs. Note that with the local mapping AppAPI probes a local
`:<image-tag>-cpu` variant first and then the exact `:<image-tag>`, so tag the build exactly as info.xml
declares it.

### 6. Point the daemon at local images and update

The `--info-xml` path is read by the Nextcloud PHP process, so on a containerized Nextcloud copy the file into
the container first:

```bash
occ app_api:daemon:registry:add <daemon> --registry-from <registry> --registry-to local
docker cp appinfo/info.xml <nextcloud-container>:/tmp/<appid>-info.xml
occ app_api:app:update <appid> --info-xml /tmp/<appid>-info.xml --wait-finish
occ app_api:daemon:registry:remove <daemon> --registry-from <registry> --registry-to local   # on shared daemons
```

Facts behind this (all verified live): the mapping makes AppAPI skip the registry pull and use the local image;
without it a local-only image aborts the deploy at pull. `--info-xml` is mandatory here because AppAPI would
otherwise consult the app store (and for the same reason, the admin UI update button would reinstall the stock
version). The update disables the app, recreates the container from the new image and re-enables it, preserving
the app secret, port, app configuration, user preferences and the data volume. Downloads the app performed into
its persistent storage (language models and the like) survive, so an update does not re-fetch them.

### 7. Verify

```bash
occ app_api:app:list                                      # new version, [enabled]
docker inspect nc_app_<appid> --format '{{.Config.Image}}'  # your tag
```

plus the functional check for the change itself (the worked example's is "the new model appears in the admin AI
settings and answers a task").

### 8. Aftermath

- Offer to upstream the change (issue + pull request); local rebuilds are for immediacy, upstream is the fix.
- To return to stock: remove the registry mapping (if still present) and run `occ app_api:app:update <appid>`
  without `--info-xml`; the app store definition and image take over again at the next store release. To pin the
  local build, keep the mapping and do nothing.

## Worked example: "Add EuroLLM support to llm2"

llm2 is Nextcloud's local large-language-model ExApp. Every model it loads becomes its own set of Task
Processing providers ("Local Large language Model: <model>"), so adding a model is user-visible in the admin AI
settings. The user asks: "I want the European EuroLLM model available in my Assistant."

The change (three files):

1. `lib/main.py`, `models_to_fetch`: one entry, commit-pinned like its neighbors (`<revision>` is the commit
   hash from the model file's HuggingFace page):
   ```python
   "https://huggingface.co/bartowski/EuroLLM-9B-Instruct-GGUF/resolve/<revision>/EuroLLM-9B-Instruct-Q4_K_M.gguf":
       {"save_path": os.path.join(persistent_storage(), "EuroLLM-9B-Instruct-Q4_K_M.gguf")},
   ```
2. `default_config/config.json`: a first-class config entry keyed by the GGUF file name: context size and
   template facts from the model card (EuroLLM: ChatML template, stop `<|im_end|>`, `n_ctx` 4096,
   `max_tokens` 2048), mirroring the shape of the existing entries.
3. `appinfo/info.xml`: version and image-tag bump.

Then steps 5 to 7: `docker build -f Dockerfile.cpu -t ghcr.io/nextcloud/llm2:<new-tag> .`, registry mapping,
`app:update --info-xml`. During init llm2 downloads only the new 5.6 GB model (its existing models sit in the
persistent volume, which survives the update); when it re-registers its providers, "Local Large language Model:
EuroLLM-9B-Instruct-Q4_K_M" appears for every text task type, ready to be selected and asked for a German or
French summary.

Verified end to end: install including the 5.6 GB model download took under ten minutes; a later code-change
redeploy took ~20 seconds with the model volume untouched; the new providers appeared for every text task type
and answered a German prompt correctly. One real-world beat from that verification: the then-current CPU image
resolved Python 3.10 while the code used an API from 3.11, crashing the task loop silently (upstream issue
nextcloud/llm2#284); the agent noticed it in the logs and handled it inside the same loop, which is exactly what
this runbook is for.

A bugfix-flavored example with the same mechanics: llm2 does not report per-token generation progress
(upstream issue nextcloud/llm2#241); the fix is a small change in its streaming loop, and reaches the running
instance through exactly steps 4 to 7.

## Troubleshooting (symptom first)

| Symptom | Cause and fix |
|---|---|
| "ExApp <id> is already updated" and nothing changed | Version not bumped; `app:update` no-ops on equal versions. |
| "Failed to get app info for '<id>' from the Appstore" | `--info-xml`/`--json-info` missing on update of a non-store version. |
| Deploy aborts at pull (`.../images/create ... 500`) | Registry mapping to `local` missing, or image name/tag does not exactly match info.xml. |
| Update succeeded but behavior unchanged | Rebuilt image under a different name/tag than info.xml declares, or the mapping was absent so the registry copy was pulled; check `docker inspect nc_app_<id> --format '{{.Config.Image}}'` and `docker images`. |
| App store update button "reinstalled" the stock app | Expected: the UI updates from the store. Local builds are occ-driven (`--info-xml`). |
| App `[enabled]` and heartbeating, but AI tasks are never picked up | The fault can be inside or outside the container. Inside: a silently crashed task loop still registers providers but never claims tasks; look for a startup traceback in `docker logs nc_app_<appid>` (real case: nextcloud/llm2#284). Outside: `occ taskprocessing:task:list` and `taskprocessing:task:get <id>` show tasks stuck scheduled, `occ background-job:list` and cron health show whether the server side runs at all, and the admin AI settings select which provider handles each task type. |
| App on a Kubernetes daemon | This runbook does not apply as written. Push the rebuilt image to a registry the nodes can pull and map the upstream registry to it (`app_api:daemon:registry:add <daemon> --registry-from ghcr.io --registry-to <mirror>`, AppAPI with nextcloud/app_api#1046), or pre-load it on every node and map to `local` (HaRP newer than 0.4.5 then sets `imagePullPolicy: Never`), then `app:update`. Or move the app to a docker daemon for the debugging session. |

## Related

- [exapp-development.md](../../exapp-development/references/exapp-development.md): the loops in a development
  setting, and the full redeploy table.
- [dev-environment.md](../../nextcloud-dev-setup/references/dev-environment.md): a sandboxed environment to
  practice this in.
- [operations.md](../../exapp-operations/references/operations.md):
  [lifecycle command semantics](../../exapp-operations/references/operations.md#6-exapp-lifecycle-occ) and
  [operations troubleshooting](../../exapp-operations/references/operations.md#10-troubleshooting-symptom-first).
- [ai-stack.md](../../nextcloud-ai-stack/references/ai-stack.md): operating the AI apps this runbook's worked
  example belongs to, including why a provider's task types appear only after init finishes.
