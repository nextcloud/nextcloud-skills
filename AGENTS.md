<!--
  - SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Agent guide to this repository

This repository contains **Agent Skills for working with Nextcloud**: self-contained instruction packages an
AI agent (or a human) can execute. If you were pointed here with a Nextcloud task, pick the matching skill and
read its `SKILL.md` first; it tells you what to read next and the rules that matter.

## Routing

| Your task looks like | Open |
|---|---|
| "Set up a Nextcloud dev environment", "bootstrap nextcloud-docker-dev", "I need a local instance for ExApp work" | [skills/nextcloud-dev-setup/SKILL.md](skills/nextcloud-dev-setup/SKILL.md) |
| "Write an ExApp" (any language), "port my service to Nextcloud", "my ExApp will not deploy/authenticate" | [skills/exapp-development/SKILL.md](skills/exapp-development/SKILL.md) |
| "App X is broken on my Nextcloud, fix it", "add capability Y to installed app X" | [skills/exapp-maintenance/SKILL.md](skills/exapp-maintenance/SKILL.md) |
| "Install AppAPI/HaRP on my server", "register a daemon (Docker/Kubernetes/remote host/AIO)", "manage or troubleshoot ExApps with occ", "move off Docker Socket Proxy" | [skills/exapp-operations/SKILL.md](skills/exapp-operations/SKILL.md) |
| "HaRP returns 401/403/404/502/503", "ExApps unreachable although the containers run", "tune, upgrade or debug HaRP itself" | [skills/harp-operations/SKILL.md](skills/harp-operations/SKILL.md) |
| "Set up AI on my Nextcloud", "the Assistant offers nothing", "my AI task never finishes", "which app provides which task type", "GPU or CPU" | [skills/nextcloud-ai-stack/SKILL.md](skills/nextcloud-ai-stack/SKILL.md) |
| "Write a Nextcloud app in PHP", "add a page/route/table/settings to my app", "test my app's UI with Playwright", "package my app for the app store" | [skills/nextcloud-php-app/SKILL.md](skills/nextcloud-php-app/SKILL.md) |

Building an ExApp or a PHP app but nothing is set up yet? Do
[nextcloud-dev-setup](skills/nextcloud-dev-setup/SKILL.md) first; exapp-development and nextcloud-php-app
assume a working environment exists.

Each skill's `SKILL.md` links the full runbooks in its `references/` directory; read the relevant runbook in
full before executing it. Cross-skill links are relative, so keep the repository layout intact when copying
skills around; if a linked file is missing from your copy, read it from
https://github.com/oleksandr-nc/nextcloud-skills

## Ground rules (all skills)

- Never paste real secrets into files, commands you log, or chat; placeholders are in angle brackets.
- Destructive actions (`docker compose down -v`, `app:unregister --rm-data`, deleting volumes or `workspace/`)
  always need explicit human approval first.
- Verify state by command instead of assuming it, and do not continue past a failed Verify block.
- These are living documents: when Nextcloud or AppAPI behavior changes, the affected runbook is updated in
  the same change (see [CONTRIBUTING.md](CONTRIBUTING.md)).

## Working on this repository itself

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the skill format, authoring rules, and validation
(`python3 scripts/validate_skills.py`). Commits are one-line, DCO-signed (`git commit -s`).
