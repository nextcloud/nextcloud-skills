<!--
  - SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->

# OpenAI-compatible API integration

A detailed runbook, part of the [nextcloud-ai-stack](../SKILL.md) skill. Local ExApp providers stay in
[ai-stack.md](ai-stack.md); this page covers **`integration_openai`**, the PHP app that talks to OpenAI or any
OpenAI-compatible HTTP API (LocalAI, Ollama, and similar).

Last verified against: Nextcloud master (36), `integration_openai` from the app store / git, with a
server-wide API key against an OpenAI-compatible endpoint, on 2026-08-26.

## What you need

| Requirement | Notes |
|---|---|
| Nextcloud + admin/`occ` | Same instance the Assistant / Task Processing UI uses |
| `integration_openai` app | Store install when compatible; otherwise clone into `apps-extra` and bump `max-version` like Assistant |
| API base URL | Empty for OpenAI defaults, or the **OpenAI-compatible** root including `/v1` when the vendor uses that layout |
| API key (or basic auth) | Admin-wide key via `occ config:app:set … --sensitive`, or per-user in personal settings |
| Working cron / background jobs | Integration providers are **synchronous**; `occ taskprocessing:worker` (or cron driving jobs) must run |
| Network egress | The Nextcloud container must reach the API host (for example `api.openai.com`) |

You do **not** need AppAPI, HaRP, or a deploy daemon for this path.

## Install the app

```bash
occ app:install integration_openai
occ app:enable integration_openai
```

If the store rejects the app as incompatible with your server major (common on master), install from source the
same way as Assistant in [ai-stack.md](ai-stack.md): clone into `apps-extra`, bump
`<nextcloud max-version="…"/>`, install PHP deps as `www-data`, then enable:

```bash
git clone --depth 1 --branch <tag> https://github.com/nextcloud/integration_openai.git \
  workspace/server/apps-extra/integration_openai
# bump max-version in appinfo/info.xml to the server major
sudo chown -R 33:33 workspace/server/apps-extra/integration_openai
docker compose exec -u www-data nextcloud bash -lc \
  'cd /var/www/html/apps-extra/integration_openai && composer install --no-dev -n --no-scripts'
# Always build frontend assets (settings UI); Node must satisfy package.json engines
# (nextcloud-docker-dev: nvm under /root/.nvm in the PHP container):
docker compose exec nextcloud bash -lc \
  '. /root/.nvm/nvm.sh 2>/dev/null; cd /var/www/html/apps-extra/integration_openai && npm ci && npm run build && chown -R www-data:www-data .'
occ app:enable integration_openai
```

Use `--no-scripts` when the package's `php-scoper` post-install hook fails in the container (verified on
source installs). Do **not** skip `npm ci && npm run build` on source installs: Task Processing providers
register from PHP alone, but the admin settings UI (URL, API key, models, modality toggles) needs the Vite
bundles under `js/` / `css/`.

### Store copy shadows `apps-extra`

If you previously ran `occ app:install integration_openai` and it landed under `apps-writable` with a
`max-version` that excludes the current server major, `occ app:enable` keeps failing with “not compatible”
even after you put a patched tree in `apps-extra`. Writable apps win over `apps-extra`.

```bash
occ app:getpath integration_openai
# if this prints …/apps-writable/integration_openai, remove the store copy first:
occ app:remove integration_openai
occ app:getpath integration_openai   # should now be …/apps-extra/integration_openai
occ app:enable integration_openai
```

Confirm with `occ app:list` that the enabled version matches the `apps-extra` `info.xml` (for example
`4.2.0`), not the abandoned store build.

## Configure (admin)

All keys live under the `integration_openai` app config. Prefer `occ` so agents and automation do not paste
secrets into the UI. Do **not** hard-code model IDs before you know what the endpoint exposes.

### 1. Credentials and endpoint

```bash
# Service URL: empty / default = OpenAI. For other OpenAI-compatible APIs set the /v1 root:
# occ config:app:set integration_openai url --value="https://api.openai.com/v1"
# occ config:app:set integration_openai service_name --value="OpenAI"

# Server-wide API key (sensitive — never commit or log the value)
occ config:app:set integration_openai api_key --value="$OPENAI_API_KEY" --sensitive

# Chat-completions endpoint (recommended for current OpenAI models and most compatible APIs)
occ config:app:set integration_openai chat_endpoint_enabled --value="1"

# Ensure the LLM provider group is on
occ config:app:set integration_openai llm_provider_enabled --value="1"
```

For non-OpenAI compatible APIs that reject `max_completion_tokens`, set:

```bash
occ config:app:set integration_openai use_max_completion_tokens_param --value="0"
```

### 2. List models from the API first

Always query `/v1/models` **before** choosing defaults. Pick IDs that appear in that list; never assume a
stock OpenAI id exists on a third-party endpoint.

```bash
# OpenAI default host; for a custom url use that host's /v1/models instead
API_BASE="${OPENAI_API_BASE:-https://api.openai.com/v1}"
docker compose exec nextcloud curl -sS -H "Authorization: Bearer $OPENAI_API_KEY" \
  "$API_BASE/models" | jq -r '.data[].id' | sort
```

Typical id patterns (use only if present in the list):

| Modality | Look for ids like | OpenAI defaults (when listed) |
|---|---|---|
| Text / chat | `gpt-*`, or the vendor's chat model names | `gpt-4.1-mini` |
| Image (t2i) | `gpt-image-*`, `dall-e-*` | `gpt-image-1-mini` |
| Speech-to-text (stt) | `whisper-*` | `whisper-1` |
| Text-to-speech (tts) | `tts-*` | `tts-1-hd` |

### 3. Set default models from the list

```bash
# Replace <…> with ids that appeared in step 2
occ config:app:set integration_openai default_completion_model_id --value="<chat-model-id>"
occ config:app:set integration_openai default_image_model_id --value="<image-model-id>"
occ config:app:set integration_openai default_stt_model_id --value="<stt-model-id>"
# TTS is stored as default_speech_model_id (not default_tts_model_id)
occ config:app:set integration_openai default_speech_model_id --value="<tts-model-id>"
```

Only set a modality's default when you also enable that modality below. Skip keys for modalities you leave
off.

### 4. Enable t2i / stt / tts only when supported

Dead providers confuse Task Processing and the Assistant. Gate each modality on real support:

| Provider | t2i / stt / tts |
|---|---|
| **OpenAI** (`url` empty or `https://api.openai.com/v1`) | Safe to enable all three; set the OpenAI default models from the table above when they appear in `/v1/models` |
| **Other OpenAI-compatible APIs** | Inspect the model list first. Enable a modality only when a matching model id is clearly present; then set that modality's default. If unsure, leave the flag `0` |

```bash
# OpenAI — enable all modalities and set defaults from the list
occ config:app:set integration_openai t2i_provider_enabled --value="1"
occ config:app:set integration_openai stt_provider_enabled --value="1"
occ config:app:set integration_openai tts_provider_enabled --value="1"
occ config:app:set integration_openai default_completion_model_id --value="gpt-4.1-mini"
occ config:app:set integration_openai default_image_model_id --value="gpt-image-1-mini"
occ config:app:set integration_openai default_stt_model_id --value="whisper-1"
occ config:app:set integration_openai default_speech_model_id --value="tts-1-hd"

# Other providers — example: chat only (no image / STT / TTS in the model list)
occ config:app:set integration_openai t2i_provider_enabled --value="0"
occ config:app:set integration_openai stt_provider_enabled --value="0"
occ config:app:set integration_openai tts_provider_enabled --value="0"
# If the list clearly has e.g. whisper-1 and tts-1, set those defaults and flip the matching flags to 1
```

After changing provider enable flags or defaults, reload PHP apps so registration picks them up:

```bash
occ app:disable integration_openai && occ app:enable integration_openai
```

### Personal keys

A user-level `api_key` in personal settings **overrides** the admin key for that user (including for some admin
UI model fetches). Prefer an empty personal key when testing the admin configuration.

### UI equivalent

**Administration settings → Artificial intelligence** (OpenAI integration section): service URL, API key,
chat-completions toggle, max_completion_tokens toggle, default models, per-modality enable switches.

## Provider selection when llm2 is also installed

Both llm2 and `integration_openai` can register `core:text2text` (and related types). Pick the default in
**Administration settings → Artificial intelligence**, or leave both and accept whichever Task Processing
selects. For a clean acceptance check against the API provider, temporarily disable the ExApp provider or set
the OpenAI integration provider as preferred for the task type under test.

## Verify

Same acceptance recipe as [ai-stack.md](ai-stack.md#verify-the-acceptance-recipe):

1. `GET /ocs/v2.php/taskprocessing/tasktypes` includes `core:text2text` (wait up to 60 s for cache TTL).
2. Schedule a `core:text2text` task with a short prompt.
3. Drive background jobs / `occ taskprocessing:worker --once` until the task is `STATUS_SUCCESSFUL`.

Quick config sanity (does not print the secret):

```bash
occ config:app:get integration_openai url
occ config:app:get integration_openai chat_endpoint_enabled
occ config:app:get integration_openai use_max_completion_tokens_param
occ config:app:get integration_openai default_completion_model_id
occ config:app:get integration_openai default_image_model_id
occ config:app:get integration_openai default_stt_model_id
occ config:app:get integration_openai default_speech_model_id
occ config:app:get integration_openai t2i_provider_enabled
occ config:app:get integration_openai stt_provider_enabled
occ config:app:get integration_openai tts_provider_enabled
occ config:app:get integration_openai api_key   # should be non-empty; do not log it
```

Confirm enabled modalities match what `/v1/models` actually offers (step 2 above).

## Endpoint cheat sheet

| Endpoint | `url` | Chat endpoint | `use_max_completion_tokens_param` | Notes |
|---|---|---|---|---|
| OpenAI | `` (empty) or `https://api.openai.com/v1` | either | `1` | App defaults assume OpenAI |
| LocalAI / Ollama (OpenAI mode) | `http://<host>:<port>/v1` | usually chat | `0` | Reachable from the Nextcloud container network |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401` on `/v1/models` | Wrong or expired key; personal key overriding admin; URL missing `/v1` |
| `400` mentioning `max_completion_tokens` | Leave `use_max_completion_tokens_param=0` for non-OpenAI APIs |
| Empty model list in admin UI | Admin viewing with a stale personal key; or egress blocked; re-run `/v1/models` from the container |
| Enabled t2i/stt/tts but tasks fail | Modality enabled without a matching model on the endpoint; disable the flag or pick a listed default |
| Blank OpenAI / AI settings page after source install | Missing Vite build — run `npm ci && npm run build` in the app directory |
| Task types missing after enable | `llm_provider_enabled=0`; or app not re-enabled after config change |
| Tasks stay `scheduled` | Background jobs / worker not running (integration providers are synchronous) |
| Enable says “not compatible” but `apps-extra` `info.xml` looks fine | An older store install under `apps-writable` is shadowing; `occ app:remove` then enable the `apps-extra` copy (see above) |

## Related

- [ai-stack.md](ai-stack.md): local providers, Assistant install, acceptance recipe.
- [ai-troubleshooting.md](ai-troubleshooting.md): symptom-first diagnosis across layers.
- Upstream app: https://github.com/nextcloud/integration_openai
- Admin manual: [AI as a Service](https://docs.nextcloud.com/server/latest/admin_manual/ai/ai_as_a_service.html)
