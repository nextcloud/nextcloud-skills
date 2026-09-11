#!/bin/sh
# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Read-only diagnostics for a HaRP container. Changes nothing and prints no secrets:
# values of HP_SHARED_KEY and friends are replaced with <set>/<unset>.
#
# Usage:
#   HARP_CONTAINER=appapi-harp NC_URL=https://cloud.example.com ./harp-triage.sh
#
# Environment:
#   HARP_CONTAINER  HaRP container name          (default: appapi-harp)
#   NC_URL          Nextcloud base URL           (optional; enables the public-path probe)
#   HP_SHARED_KEY   shared key                   (optional; enables the signed /info probe.
#                                                 Read from the container when not given)

set -u

HARP_CONTAINER=${HARP_CONTAINER:-appapi-harp}
NC_URL=${NC_URL:-}
SECRET_VARS="HP_SHARED_KEY HP_SHARED_KEY_FILE HP_K8S_BEARER_TOKEN"

section() { printf '\n=== %s ===\n' "$1"; }

if ! docker inspect "$HARP_CONTAINER" >/dev/null 2>&1; then
    echo "No such container: $HARP_CONTAINER (set HARP_CONTAINER)" >&2
    exit 1
fi

section "Container"
docker inspect "$HARP_CONTAINER" --format \
    'name={{.Name}}
image={{.Config.Image}}
state={{.State.Status}}
health={{if .State.Health}}{{.State.Health.Status}}{{else}}no healthcheck{{end}}
restarts={{.RestartCount}}
started={{.State.StartedAt}}'

# Collected before the probes below, so this tool's own requests do not show up as findings.
# 401 and 404 are normal here (unsigned probes, GET /), so only 5xx and real errors are interesting.
recent_errors=$(docker logs --since 30m "$HARP_CONTAINER" 2>&1 \
    | grep -aEi ' 5[0-9][0-9] [0-9]+ |error|traceback|exception' \
    | tail -20)

section "Published ports"
docker port "$HARP_CONTAINER" || echo "(none published; reachable only on its Docker network)"

section "Configuration (secret values redacted)"
docker inspect "$HARP_CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep -E '^(HP_|NC_)' \
    | while IFS='=' read -r key value; do
        for secret in $SECRET_VARS; do
            if [ "$key" = "$secret" ]; then
                [ -n "$value" ] && value='<set>' || value='<unset>'
                break
            fi
        done
        printf '%s=%s\n' "$key" "$value"
    done

section "Probes"
key=${HP_SHARED_KEY:-$(docker inspect "$HARP_CONTAINER" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^HP_SHARED_KEY=//p')}
if [ -z "$key" ]; then
    # HP_SHARED_KEY_FILE setups (the dev environment's default): read the mounted file
    key_file=$(docker inspect "$HARP_CONTAINER" \
        --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^HP_SHARED_KEY_FILE=//p')
    [ -n "$key_file" ] && key=$(docker exec "$HARP_CONTAINER" cat "$key_file" 2>/dev/null)
fi

if [ -n "$key" ]; then
    printf 'info (expect version + "docker": true): '
    docker exec "$HARP_CONTAINER" curl -s --max-time 10 \
        -H "harp-shared-key: $key" http://127.0.0.1:8780/exapps/app_api/info || echo "(request failed)"
    printf '\n'
else
    echo 'info: skipped (neither HP_SHARED_KEY nor HP_SHARED_KEY_FILE available)'
fi

printf 'unsigned /exapps/app_api/info (expect 401): '
docker exec "$HARP_CONTAINER" curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 \
    http://127.0.0.1:8780/exapps/app_api/info || echo "(request failed)"

printf 'GET / (expect 404 by design): '
docker exec "$HARP_CONTAINER" curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 \
    http://127.0.0.1:8780/ || echo "(request failed)"

if [ -n "$NC_URL" ]; then
    printf 'public %s/exapps/app_api/info (401 good, 404 rule missing, 502 rule points nowhere): ' "$NC_URL"
    curl -s -o /dev/null -w '%{http_code}\n' --max-time 15 "$NC_URL/exapps/app_api/info" \
        || echo "(request failed)"
else
    echo 'public path: skipped (set NC_URL)'
fi

section "ExApp containers"
docker ps -a --filter name=nc_app_ --format '{{.Names}}\t{{.Status}}\t{{.Image}}' \
    | sed 's/^/  /' || true
[ -z "$(docker ps -aq --filter name=nc_app_)" ] && echo "  (none)"

section "Recent 5xx and errors (last 30 minutes, collected before these probes ran)"
if [ -n "$recent_errors" ]; then
    printf '%s\n' "$recent_errors" | sed 's/^/  /'
else
    echo "  (none)"
fi

printf '\nDone. Nothing above was modified.\n'
