---
name: nextcloud-ai-stack
description: >-
  Installs, verifies and debugs Nextcloud's AI features end to end: the Assistant frontend, the server's Task
  Processing engine, and the provider apps behind it (llm2, translate2, stt_whisper2,
  text2image_stablediffusion2, context_chat) whether they run as local ExApps or as external API
  integrations. Use for "set up AI on my Nextcloud", "which app provides which task type", "the Assistant
  shows nothing", "my AI task never finishes", GPU versus CPU decisions, and model storage or download
  problems.
license: AGPL-3.0-or-later
compatibility: >-
  Nextcloud 33–36 with admin (occ) access; local providers additionally need AppAPI with a working deploy
  daemon. Last verified with Nextcloud master (36), AppAPI 36.0.0-dev.0, HaRP 0.4.4, llm2 2.8.0,
  integration_openai, on 2026-08-26.
---

# The Nextcloud AI stack

Three layers, and almost every support question is really about the boundary between them:

- **Frontends**: Assistant, Talk, Files, Mail. They ask for work, they never compute it.
- **Task Processing**: the scheduler inside the Nextcloud server. It owns task types, picks a provider,
  queues the task and runs it through background jobs.
- **Providers**: the apps that actually compute. Either local ExApps deployed through AppAPI (llm2,
  translate2, stt_whisper2, text2image_stablediffusion2, context_chat_backend) or integrations that call an
  external API (integration_openai and friends).

"AI does nothing" almost always means no provider is registered for the task type the frontend asked for.

## How to work

1. Installing or extending the stack: [references/ai-stack.md](references/ai-stack.md). It has the install
   order, the acceptance checks, and the model storage and GPU facts that decide your hardware.
2. OpenAI integration (OpenAI or OpenAI-compatible APIs) — prefer this over local llm2 when you lack
   GPU/disk for multi-GB models: [references/openai-integration.md](references/openai-integration.md).
3. Something is broken: [references/ai-troubleshooting.md](references/ai-troubleshooting.md), which starts
   from the symptom and the one command that splits the problem in half.
4. Collect state first: [assets/ai-doctor.sh](assets/ai-doctor.sh) prints registered task types, provider
   apps, ExApp init progress, stuck tasks and background-job health.

## Facts that save hours

- The provider list is the ground truth: `GET /ocs/v2.php/taskprocessing/tasktypes` returns **only task types
  that currently have a provider**. If `core:text2text` is absent, no amount of Assistant debugging helps.
  It is cached for 60 seconds, so re-query once before believing a short list.
- A provider whose task loop crashed still answers heartbeats, still registers its providers and still shows
  its task types. "Healthy" proves nothing; the proof is whether it calls
  `taskprocessing/tasks_provider/next`.
- `occ app_api:app:list` prints `[enabled]` from registration onwards, which does **not** mean the app is
  ready. A provider ExApp registers its providers only after `/init` completes, and init is where multi-GB
  model downloads happen (verified: llm2 sat at init 15% with the app already listed as enabled).
- Model downloads live in the ExApp's persistent volume. `app_api:app:unregister --rm-data` deletes them and
  costs you the whole download again.
- Task Processing runs through background jobs: if cron is broken, tasks stay `scheduled` forever and nothing
  in the AI apps is at fault.
- Compute device is a property of the **daemon**, not the app: register the daemon with
  `--compute_device cuda|rocm` before deploying GPU providers.
- On the Nextcloud 35+ dev line, several AI ExApps have no release in the app store feed yet, so they install
  from a manifest instead of the store. Manifest `max-version` and PHP apps like Assistant may also lag the
  server major — bump a local copy (see ai-stack.md).
- Stock llm2 init downloads multiple multi-GB models. On Cloud Agents and CPU hosts without that budget, use
  [openai-integration.md](references/openai-integration.md) instead of registering llm2.
- `--info-xml` paths must be readable by the Nextcloud PHP process (inside the container on docker-dev).
- PHP apps from source (Assistant, `integration_openai`, …) need `npm ci && npm run build` after
  `composer install`; git does not ship the Vite `js/` / `css/` output.

## Files

- [references/ai-stack.md](references/ai-stack.md): install, verify, operate, size.
- [references/openai-integration.md](references/openai-integration.md): `integration_openai` + API keys / URLs.
- [references/ai-troubleshooting.md](references/ai-troubleshooting.md): symptom-first diagnosis.
- [assets/ai-doctor.sh](assets/ai-doctor.sh): read-only state collector.
- Deploying and managing the provider ExApps themselves:
  [exapp-operations](../exapp-operations/SKILL.md). Changing one of them:
  [exapp-maintenance](../exapp-maintenance/SKILL.md). Writing a provider:
  [exapp-development](../exapp-development/SKILL.md).
