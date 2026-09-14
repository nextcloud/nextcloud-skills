#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Acceptance harness: run a skill's claims against a live Nextcloud and report.

The runbooks in this repository promise verified behaviour. This turns the load-bearing
promises into executable checks, so a maintainer can prove a skill still holds before
refreshing its "Last verified against" line.

Usage:
    python3 tests/verify.py --list
    python3 tests/verify.py --skill harp-operations
    python3 tests/verify.py --all                 # skips slow checks
    python3 tests/verify.py --all --include-slow  # also deploys the reference ExApp

Configuration (environment):
    NC_CONTAINER    Nextcloud container name              (default: master-nextcloud-1)
    OCC             full occ command, overrides NC_CONTAINER
    NC_URL          Nextcloud base URL                    (default: http://nextcloud.local)
    DAEMON          docker-install HaRP daemon name       (default: local-harp)
    HARP_CONTAINER  HaRP container name                   (default: appapi-harp)
    PROXY_CONTAINER nginx-proxy container name            (default: master-proxy-1)
    NC_ADMIN / NC_ADMIN_PASS   admin credentials for OCS checks (optional)
    NC_APPS_DIR     host path of a directory on the instance's apps_paths (optional; enables the
                    slow nextcloud-php-app check, e.g. workspace/server/apps-extra)
    NC_APPS_DIR_IN_CONTAINER   the same directory as seen inside NC_CONTAINER (optional; adds the
                    PHPUnit run to that check, e.g. /var/www/html/apps-extra)

Checks never touch anything but their own throwaway objects, and clean up after themselves.
"""

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF_APP = ROOT / "skills" / "exapp-development" / "assets" / "minimal_exapp"
APP_ID = "minimal_exapp"
REF_PHP_APP = ROOT / "skills" / "nextcloud-php-app" / "assets" / "minimal_php_app"
PHP_APP_ID = "minimal_php_app"

NC_CONTAINER = os.environ.get("NC_CONTAINER", "master-nextcloud-1")
OCC = os.environ.get("OCC", f"docker exec -u www-data {NC_CONTAINER} php occ")
NC_URL = os.environ.get("NC_URL", "http://nextcloud.local").rstrip("/")
DAEMON = os.environ.get("DAEMON", "local-harp")
HARP_CONTAINER = os.environ.get("HARP_CONTAINER", "appapi-harp")
PROXY_CONTAINER = os.environ.get("PROXY_CONTAINER", "master-proxy-1")
NC_ADMIN = os.environ.get("NC_ADMIN", "")
NC_ADMIN_PASS = os.environ.get("NC_ADMIN_PASS", "")
NC_APPS_DIR = os.environ.get("NC_APPS_DIR", "")
NC_APPS_DIR_IN_CONTAINER = os.environ.get("NC_APPS_DIR_IN_CONTAINER", "")


class Skip(Exception):
    """Raised by a check when its precondition is not met."""


def run(cmd, check=False, timeout=600):
    proc = subprocess.run(
        cmd if isinstance(cmd, list) else shlex.split(cmd),
        capture_output=True, text=True, timeout=timeout,
    )
    out = "\n".join(line for line in (proc.stdout + proc.stderr).splitlines()
                    if "Profiler output available" not in line)
    if check and proc.returncode != 0:
        raise AssertionError(f"command failed ({proc.returncode}): {cmd}\n{out.strip()[:400]}")
    return proc.returncode, out


def occ(args, check=False, timeout=600):
    return run(f"{OCC} {args}", check=check, timeout=timeout)


def http_code(url, extra=""):
    _, out = run(f"curl -s -o /dev/null -w %{{http_code}} --max-time 20 {extra} {url}")
    return out.strip()


def container_env(name):
    code, out = run(["docker", "inspect", name, "--format",
                     "{{range .Config.Env}}{{println .}}{{end}}"])
    if code != 0:
        raise AssertionError(f"no such container: {name}")
    env = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            env[key] = value
    return env


# --------------------------------------------------------------------------- checks

def harp_info():
    """HaRP /info reports a semver version and Docker support."""
    env = container_env(HARP_CONTAINER)
    key = env.get("HP_SHARED_KEY", "")
    if not key and env.get("HP_SHARED_KEY_FILE"):
        _, key = run(["docker", "exec", HARP_CONTAINER, "cat", env["HP_SHARED_KEY_FILE"]])
        key = key.strip()
    if not key:
        raise Skip("neither HP_SHARED_KEY nor HP_SHARED_KEY_FILE readable from the container")
    _, out = run(["docker", "exec", HARP_CONTAINER, "curl", "-s", "--max-time", "10",
                  "-H", f"harp-shared-key: {key}",
                  "http://127.0.0.1:8780/exapps/app_api/info"], check=True)
    data = json.loads(out)
    assert data.get("docker") is True, f"docker not reported: {out[:120]}"
    parts = str(data.get("version", "")).split(".")
    assert len(parts) == 3, f"version is not x.y.z: {data.get('version')!r}"
    return f"version={data['version']} docker=true"


def harp_root_404():
    """GET / on the ExApps frontend answers 404 by design."""
    _, out = run(["docker", "exec", HARP_CONTAINER, "curl", "-s", "-o", "/dev/null",
                  "-w", "%{http_code}", "--max-time", "10", "http://127.0.0.1:8780/"], check=True)
    assert out.strip() == "404", f"expected 404, got {out.strip()}"
    return "404 as documented"


def harp_unsigned_401():
    """An unsigned control request is rejected with 401."""
    _, out = run(["docker", "exec", HARP_CONTAINER, "curl", "-s", "-o", "/dev/null",
                  "-w", "%{http_code}", "--max-time", "10",
                  "http://127.0.0.1:8780/exapps/app_api/info"], check=True)
    assert out.strip() == "401", f"expected 401, got {out.strip()}"
    return "401 as documented"


def harp_healthy():
    """The container's own healthcheck reports healthy."""
    code, out = run(["docker", "inspect", HARP_CONTAINER, "--format",
                     "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"])
    assert code == 0, f"no such container: {HARP_CONTAINER}"
    assert out.strip() == "healthy", f"health is {out.strip()!r}"
    return "healthy"


def harp_rewrites_aa_version():
    """HaRP overwrites AA-VERSION, so ExApps must not validate it strictly."""
    code, out = run(["docker", "exec", HARP_CONTAINER, "grep", "-c",
                     "http-request set-header AA-VERSION", "/run/harp/haproxy.cfg"])
    assert code == 0 and int(out.strip() or 0) > 0, "no AA-VERSION rewrite in the generated config"
    return f"{out.strip()} rewrite rules in haproxy.cfg"


def public_exapps_path():
    """The /exapps/ reverse-proxy rule reaches HaRP (401), which installs depend on."""
    code = http_code(f"{NC_URL}/exapps/app_api/info")
    assert code == "401", (
        f"expected 401, got {code}: 404 means the rule is missing, 502 means it points nowhere. "
        "ExApp installs fail without this path")
    return "401 through the public URL"


def proxy_survives_harp_absence():
    """The proxy config is valid and the /exapps/ rule resolves HaRP per request, so nginx starts without it."""
    host = NC_URL.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    code, out = run(["docker", "exec", PROXY_CONTAINER, "nginx", "-t"])
    assert code == 0, f"nginx -t failed in {PROXY_CONTAINER}: {out.strip()[-200:]}"
    code, snippet = run(["docker", "exec", PROXY_CONTAINER, "cat", f"/etc/nginx/vhost.d/{host}"])
    assert code == 0, f"no /etc/nginx/vhost.d/{host} in {PROXY_CONTAINER} (Stage 5)"
    targets = [line.split(None, 1)[1].rstrip(";") for line in snippet.splitlines()
               if line.strip().startswith("proxy_pass")]
    assert targets, "no proxy_pass in the vhost snippet"
    literal = [t for t in targets if "$" not in t]
    assert not literal, (
        f"literal proxy_pass {literal[0]}: nginx refuses to start whenever that host is absent, "
        "taking every instance down; use the set $harp_upstream form from Stage 5")
    return "nginx -t ok; proxy_pass resolves at request time"


def notifications_app_enabled():
    """The notifications app is enabled; without it AppAPI's notification endpoint delivers to nobody."""
    _, out = occ("app:list", check=True)
    enabled = out.split("Disabled:", 1)[0]
    assert "  - notifications:" in enabled, (
        "notifications app is not enabled: server git checkouts do not ship it and the app store has "
        "no build for a development version (dev-environment.md Stage 3)")
    return "notifications enabled"


def occ_command_surface():
    """Every occ command the operations runbook documents exists."""
    _, out = occ("list app_api", check=True)
    documented = [
        "app_api:app:register", "app_api:app:unregister", "app_api:app:enable",
        "app_api:app:disable", "app_api:app:update", "app_api:app:list",
        "app_api:app:config:get", "app_api:app:config:set", "app_api:app:config:delete",
        "app_api:app:config:list", "app_api:daemon:register", "app_api:daemon:unregister",
        "app_api:daemon:list", "app_api:daemon:registry:add", "app_api:daemon:registry:list",
        "app_api:daemon:registry:remove",
    ]
    missing = [c for c in documented if c not in out]
    assert not missing, f"missing: {', '.join(missing)}"
    return f"{len(documented)} documented commands present"


def daemon_register_is_noop():
    """Re-registering an existing daemon name is a no-op that still exits 0."""
    code, out = occ(f'app_api:daemon:register {DAEMON} "x" manual-install http x http://x')
    assert "skipped" in out.lower(), f"expected a skip message, got: {out.strip()[:160]}"
    assert code == 0, f"expected exit 0, got {code}"
    return "prints 'Registration skipped' and exits 0"


def daemon_registry_roundtrip():
    """Registry mappings can be added, listed and removed."""
    marker = "verify.example.invalid"
    occ(f"app_api:daemon:registry:add {DAEMON} --registry-from {marker} --registry-to local", check=True)
    try:
        _, out = occ(f"app_api:daemon:registry:list {DAEMON}", check=True)
        assert marker in out, "mapping not listed after adding it"
    finally:
        occ(f"app_api:daemon:registry:remove {DAEMON} --registry-from {marker} --registry-to local")
    _, out = occ(f"app_api:daemon:registry:list {DAEMON}")
    assert marker not in out, "mapping still present after removal"
    return "add, list and remove behave"


def taskprocessing_surface():
    """The Task Processing occ commands the AI runbook uses exist."""
    _, out = occ("list taskprocessing", check=True)
    documented = ["taskprocessing:task:list", "taskprocessing:task:get",
                  "taskprocessing:task:stats", "taskprocessing:task:cleanup",
                  "taskprocessing:task-type:set-enabled", "taskprocessing:worker"]
    missing = [c for c in documented if c not in out]
    assert not missing, f"missing: {', '.join(missing)}"
    return f"{len(documented)} commands present"


def tasktypes_endpoint():
    """The task-type endpoint answers and lists only types that have a provider."""
    if not (NC_ADMIN and NC_ADMIN_PASS):
        raise Skip("set NC_ADMIN and NC_ADMIN_PASS")
    _, out = run(["curl", "-s", "-u", f"{NC_ADMIN}:{NC_ADMIN_PASS}", "-H", "OCS-APIRequest: true",
                  f"{NC_URL}/ocs/v2.php/taskprocessing/tasktypes?format=json"], check=True)
    types = json.loads(out)["ocs"]["data"]["types"]
    # PHP serialises an empty associative array as [], so an instance with no providers
    # answers "types": [] where one with providers answers an object. Both are valid.
    assert isinstance(types, (dict, list)), f"unexpected payload shape: {type(types).__name__}"
    if isinstance(types, list):
        assert not types, f"a list payload is only valid when empty, got {len(types)} entries"
        return "0 task types have a provider (none installed)"
    return f"{len(types)} task types have a provider"


def cron_is_recent():
    """Task Processing depends on background jobs, so cron must be running."""
    _, out = occ("config:app:get core lastcron")
    value = out.strip()
    if not value.isdigit():
        raise Skip("core.lastcron unreadable")
    age = int(time.time()) - int(value)
    assert age < 900, f"last cron run was {age}s ago; tasks will sit in 'scheduled'"
    return f"last run {age}s ago"


def reference_exapp_lifecycle():
    """SLOW: deploy the reference ExApp and assert the contract the dev runbook promises."""
    _, out = occ("app_api:app:list")
    if APP_ID in out:
        raise Skip(f"{APP_ID} is already registered here; refusing to touch it")

    tag = "example.local/minimal_exapp"
    version = "1.0.0"
    run(f"docker build -t {tag}:{version} {REF_APP}", check=True, timeout=900)
    occ(f"app_api:daemon:registry:add {DAEMON} --registry-from example.local --registry-to local")
    run(["docker", "cp", str(REF_APP / "appinfo" / "info.xml"),
         f"{NC_CONTAINER}:/tmp/{APP_ID}-verify.xml"], check=True)
    notes = []
    try:
        occ(f"app_api:app:register {APP_ID} {DAEMON} --info-xml /tmp/{APP_ID}-verify.xml --wait-finish",
            check=True, timeout=900)

        _, listed = occ("app_api:app:list", check=True)
        assert f"{APP_ID} (" in listed and "[enabled]" in listed, "app is not enabled after register"

        _, echo = run(f"curl -s --max-time 20 {NC_URL}/exapps/{APP_ID}/echo", check=True)
        assert json.loads(echo)["app_id"] == APP_ID, f"unexpected /echo body: {echo[:120]}"
        notes.append("public route serves")

        assert http_code(f"{NC_URL}/exapps/{APP_ID}/not-declared") == "404", "undeclared path is not 404"
        notes.append("undeclared path 404")

        env = container_env(f"nc_app_{APP_ID}")
        required = ["APP_ID", "APP_SECRET", "APP_PORT", "APP_HOST", "APP_VERSION",
                    "APP_PERSISTENT_STORAGE", "NEXTCLOUD_URL", "COMPUTE_DEVICE", "AA_VERSION",
                    "HP_SHARED_KEY", "HP_FRP_ADDRESS", "HP_FRP_PORT"]
        missing = [k for k in required if k not in env]
        assert not missing, f"env missing: {', '.join(missing)}"
        notes.append(f"{len(required)} env vars injected")

        secret_before = hashlib.sha256(env["APP_SECRET"].encode()).hexdigest()
        port_before = env["APP_PORT"]

        _, out = occ(f"app_api:app:update {APP_ID} --info-xml /tmp/{APP_ID}-verify.xml --wait-finish",
                     timeout=900)
        assert "already updated" in out, f"same-version update was not a no-op: {out.strip()[:160]}"
        notes.append("same-version update no-ops")

        env_after = container_env(f"nc_app_{APP_ID}")
        assert hashlib.sha256(env_after["APP_SECRET"].encode()).hexdigest() == secret_before, \
            "app secret changed"
        assert env_after["APP_PORT"] == port_before, "app port changed"
        notes.append("secret and port stable")

        code, _ = run(["docker", "volume", "inspect", f"nc_app_{APP_ID}_data"])
        assert code == 0, "persistent volume was not created"
        notes.append("data volume present")
    finally:
        occ(f"app_api:app:unregister {APP_ID} --force --rm-data --silent", timeout=600)
        occ(f"app_api:daemon:registry:remove {DAEMON} "
            f"--registry-from example.local --registry-to local")
        run(["docker", "exec", NC_CONTAINER, "rm", "-f", f"/tmp/{APP_ID}-verify.xml"])
    return "; ".join(notes)


def reference_php_app_lifecycle():
    """SLOW: install the reference PHP app from the assets and assert what the runbook promises."""
    if not NC_APPS_DIR:
        raise Skip("NC_APPS_DIR not set (host path of a directory on the instance's apps_paths)")
    if not (NC_ADMIN and NC_ADMIN_PASS):
        raise Skip("NC_ADMIN / NC_ADMIN_PASS not set")
    target = Path(NC_APPS_DIR) / PHP_APP_ID
    if target.exists():
        raise Skip(f"{target} already exists; refusing to touch it")
    _, listed = occ("app:list")
    if f"- {PHP_APP_ID}:" in listed:
        raise Skip(f"{PHP_APP_ID} is already installed here; refusing to touch it")

    auth = f"-u {shlex.quote(NC_ADMIN)}:{shlex.quote(NC_ADMIN_PASS)} -H OCS-APIRequest:true"
    base = f"{NC_URL}/index.php/apps/{PHP_APP_ID}"
    notes = []
    run(["rsync", "-a", "--exclude", "node_modules", "--exclude", "vendor", "--exclude", "build",
         "--exclude", "test-results", "--exclude", "playwright-report",
         f"{REF_PHP_APP}/", f"{target}/"], check=True)
    try:
        occ(f"app:enable {PHP_APP_ID}", check=True)
        notes.append("enabled from a plain copy, no build step")

        assert http_code(f"{base}/") == "401", "anonymous page request is not 401"
        notes.append("protected route answers 401, not 404")

        assert http_code(f"{base}/api/whoami", f"-u {shlex.quote(NC_ADMIN)}:{shlex.quote(NC_ADMIN_PASS)}") \
            == "412", "GET without OCS-APIRequest is not 412"
        _, who = run(f"curl -s --max-time 20 {auth} {base}/api/whoami", check=True)
        assert json.loads(who)["user"] == NC_ADMIN, f"unexpected whoami: {who[:120]}"
        notes.append("OCS-APIRequest header satisfies the CSRF check")

        code = http_code(f"{base}/api/items", f"{auth} -H Content-Type:application/json "
                         f"-X POST -d '{{\"title\":\"verify\"}}'")
        assert code == "201", f"POST /api/items answered {code}"
        _, items = run(f"curl -s --max-time 20 {auth} {base}/api/items", check=True)
        assert any(i.get("title") == "verify" for i in json.loads(items)), "created item not listed"
        notes.append("migration ran on enable, entity round-trips")

        assert http_code(f"{NC_URL}/index.php/settings/admin/{PHP_APP_ID}",
                         f"-u {shlex.quote(NC_ADMIN)}:{shlex.quote(NC_ADMIN_PASS)}") == "200", \
            "admin settings page is not 200"
        notes.append("admin settings section registered")

        if NC_APPS_DIR_IN_CONTAINER:
            code, out = run(["docker", "exec", "-u", "www-data", "-w",
                             f"{NC_APPS_DIR_IN_CONTAINER}/{PHP_APP_ID}", NC_CONTAINER,
                             "phpunit", "-c", "tests/phpunit.xml"], timeout=600)
            assert code == 0 and "OK (" in out, f"phpunit failed: {out.strip()[-300:]}"
            notes.append("phpunit unit + integration suites pass in the container")
    finally:
        occ(f"app:disable {PHP_APP_ID}")
        run(["rm", "-rf", str(target)])
    return "; ".join(notes) + " (the app's table, appconfig and oc_migrations rows are left behind, as for any removed app)"


CHECKS = {
    "harp-operations": [harp_info, harp_healthy, harp_root_404, harp_unsigned_401,
                        harp_rewrites_aa_version],
    "exapp-operations": [occ_command_surface, daemon_register_is_noop, daemon_registry_roundtrip,
                         public_exapps_path],
    "nextcloud-ai-stack": [taskprocessing_surface, tasktypes_endpoint, cron_is_recent],
    "nextcloud-dev-setup": [public_exapps_path, proxy_survives_harp_absence, harp_info,
                            notifications_app_enabled],
    "exapp-development": [reference_exapp_lifecycle],
    "nextcloud-php-app": [reference_php_app_lifecycle],
}
SLOW = {"reference_exapp_lifecycle", "reference_php_app_lifecycle"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skill", action="append", help="skill to verify (repeatable)")
    parser.add_argument("--all", action="store_true", help="verify every skill")
    parser.add_argument("--list", action="store_true", help="list skills and their checks")
    parser.add_argument("--include-slow", action="store_true",
                        help="also run checks that deploy containers")
    args = parser.parse_args()

    if args.list:
        for skill, checks in CHECKS.items():
            print(skill)
            for check in checks:
                slow = " (slow)" if check.__name__ in SLOW else ""
                print(f"  {check.__name__}{slow}: {(check.__doc__ or '').strip().splitlines()[0]}")
        return 0

    skills = list(CHECKS) if args.all else (args.skill or [])
    if not skills:
        parser.error("pass --skill NAME, --all or --list")
    unknown = [s for s in skills if s not in CHECKS]
    if unknown:
        parser.error(f"unknown skill(s): {', '.join(unknown)}")

    print(f"occ: {OCC}\nurl: {NC_URL}\ndaemon: {DAEMON}\nharp: {HARP_CONTAINER}\n")
    failed = passed = skipped = 0
    for skill in skills:
        print(f"--- {skill}")
        for check in CHECKS[skill]:
            if check.__name__ in SLOW and not args.include_slow:
                print(f"  SKIP {check.__name__}: slow, pass --include-slow")
                skipped += 1
                continue
            try:
                detail = check()
            except Skip as exc:
                print(f"  SKIP {check.__name__}: {exc}")
                skipped += 1
            except Exception as exc:  # noqa: BLE001 - report, do not abort the run
                print(f"  FAIL {check.__name__}: {exc}")
                failed += 1
            else:
                print(f"  PASS {check.__name__}: {detail}")
                passed += 1

    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
