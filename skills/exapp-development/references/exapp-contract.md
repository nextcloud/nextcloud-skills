<!--
  - SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->

# The ExApp manifest (`<external-app>`) reference

A reference page, part of the [exapp-development](../SKILL.md) skill. Read this when writing an ExApp's
`info.xml`, preparing a `--json-info` payload, or debugging why a route, env var, or mount did not behave as
expected. The runtime side (env vars AppAPI injects, init/heartbeat, auth headers) is in
[operations.md](../../exapp-operations/references/operations.md#9-runtime-and-the-exapp-contract).

Parsing lives in `ExAppService::getAppInfo()` (`lib/Service/ExAppService.php`); route validation in
`lib/Service/ExAppRouteHelper.php` (paths refer to the [nextcloud/app_api](https://github.com/nextcloud/app_api)
sources). Invalid route definitions abort registration with a descriptive error.

Last verified against: Nextcloud master (35), AppAPI 35.0.0-dev.1, HaRP 0.4.3, on 2026-08-11.

## The element tree

An ExApp's `info.xml` is a normal Nextcloud app manifest plus an `<external-app>` section:

```xml
<external-app>
    <docker-install>
        <registry>ghcr.io</registry>            <!-- default: docker.io -->
        <image>nextcloud/my-exapp</image>       <!-- default: the app id -->
        <image-tag>latest</image-tag>           <!-- default: latest -->
    </docker-install>

    <routes>
        <route>
            <url>^/api/.*</url>                              <!-- required; regex on the request path -->
            <verb>GET,POST</verb>                            <!-- required; comma list -->
            <access_level>USER</access_level>                <!-- required; PUBLIC | USER | ADMIN (or 0|1|2) -->
            <bruteforce_protection>[401,429]</bruteforce_protection>  <!-- optional; JSON int array -->
            <headers_to_exclude>["Cookie"]</headers_to_exclude>       <!-- optional; JSON string array -->
        </route>
    </routes>

    <environment-variables>
        <variable>
            <name>MY_SETTING</name>              <!-- key; required -->
            <display-name>My setting</display-name>
            <description>Shown in the UI</description>
            <default>some-value</default>        <!-- becomes the value unless overridden -->
        </variable>
    </environment-variables>

    <k8s-service-roles>                          <!-- optional; Kubernetes multi-Deployment apps -->
        <role>
            <name>api</name>                     <!-- role id / Deployment suffix -->
            <display-name>API</display-name>     <!-- optional; defaults to name -->
            <env>SERVICE_ROLE=api</env>          <!-- extra env line for this role's container -->
            <expose>true</expose>                <!-- true = gets a Kubernetes Service -->
        </role>
    </k8s-service-roles>
</external-app>
```

With `occ app_api:app:register --json-info`, the same keys are given as JSON (`docker-install`, `routes`,
`k8s-service-roles` may sit at the JSON root). Note the naming split: elements are hyphenated
(`docker-install`, `image-tag`, `display-name`), but route fields are underscored (`access_level`,
`bruteforce_protection`, `headers_to_exclude`).

Things the manifest does **not** declare:

- **Port and secret**: AppAPI assigns a free `APP_PORT` and generates `APP_SECRET` at registration. (The XML
  manifest cannot declare them; an `--json-info` registration payload MAY pin `secret` and `port`, see the
  fast loop in [exapp-development.md](exapp-development.md).)
- **API scopes**: removed from AppAPI; ExApps no longer declare scopes anywhere (`--force-scopes` is a
  deprecated no-op). Do not add a scopes element.
- **Mounts**: there is no `<mounts>` element (see below).

## Routes: access levels and enforcement

Every HTTP surface the ExApp exposes must be covered by a `<route>`; unmatched requests are rejected.

| Field | Meaning |
|---|---|
| `url` | Regex matched against the request path including its leading `/` (e.g. `^/api/.*`); the PHP proxy ignores case, HaRP does not |
| `verb` | Comma-separated HTTP methods the route accepts |
| `access_level` | `PUBLIC` (0) anyone, `USER` (1) any logged-in user, `ADMIN` (2) admins only |
| `bruteforce_protection` | JSON array of response status codes that count as a bruteforce attempt (e.g. `[401,429]`) |
| `headers_to_exclude` | JSON array of request header names stripped before forwarding |

Enforcement happens in two places, depending on the path a request takes:

- **HaRP path** (browser to `/exapps/...`): AppAPI hands HaRP the route table (url + verb + access_level +
  bruteforce_protection) via the ExApp metadata endpoint, and HaRP resolves the caller's level per request
  through the user-info endpoint (no/disabled user = PUBLIC, admin = ADMIN, else USER), enforcing the
  comparison itself. Since HaRP 0.4.8 the first route whose `url` matches the path and whose `verb` lists the
  method decides, as in the PHP proxy; `HEAD` is accepted wherever `GET` is listed, and a method that no route
  of the path lists gets `405` with an `Allow` header. HaRP 0.4.7 and older, and HaRP with an AppAPI that does
  not send the verbs yet, match by `url` only: the first matching route decides for every method.
  `headers_to_exclude` is not part of the HaRP checks. Observed replies: an anonymous request to a `USER` or
  `ADMIN` route gets `403` (an admin session or basic auth gets through), an undeclared path gets `404`, and the
  lifecycle endpoints (`/heartbeat`, `/init`, `/enabled`) get `502`.
- **PHP proxy path** (`/index.php/apps/app_api/proxy/...`): `ExAppProxyController` takes the first route whose
  `url` (regex) matches and whose `verb` lists the method (`HEAD` also where `GET` is listed, since
  nextcloud/app_api#1144), enforces the access level, strips `headers_to_exclude`, and applies bruteforce
  throttling on the listed status codes. A request no route lets through gets `404`. Only `GET`, `HEAD`, `POST`,
  `PUT` and `DELETE` reach the proxy at all: Nextcloud answers other methods, `PATCH` and `OPTIONS` included,
  with `405`.

Authoring notes: list every method a route serves in its `verb` (`GET,POST`), and give methods that need
different access levels different URLs, as older HaRP versions decide by URL only. An empty element
(`<headers_to_exclude></headers_to_exclude>`) is fine and means "none", but nested sub-elements
(`<bruteforce_protection><status>401</status></bruteforce_protection>`) are rejected; use the JSON-in-text form
shown above.

## Environment variables: a declared allow-list

`<environment-variables>` is an **allow-list** with defaults, not free-form input:

- Each declared `<variable>` starts with `value = default`.
- `occ app_api:app:register --env NAME=VALUE` overrides **only declared names**; undeclared `--env` values are
  **silently dropped**. If the manifest has no `<environment-variables>` block at all, every `--env` is
  ignored.
- Variables whose final value is an empty string are not passed to the container at all (verified for both a
  missing `<default>` element and `--env NAME=`).
- **Never write an empty `<default></default>` or `<default/>`**: it does not mean "empty string". The empty
  element parses to an empty array, survives the empty-value filter in `ExAppService::getAppInfo()`, and the
  variable reaches the container as the literal string `Array` (verified on AppAPI 35.0.0-dev.1). Omit the
  element entirely to leave a variable unset.
- The surviving set is stored and replayed on `app:update` (which has no `--env`/`--mount` options of its own);
  to change a value, re-register the app.

So "my `--env` did nothing" almost always means the variable is not declared in the manifest.

## Mounts: CLI-only, Docker-only

There is **no** mounts element in the manifest. `occ app_api:app:register --mount SRC:DST[:ro|rw]`
(repeatable, default `rw`) is the only source of extra mounts, and every given mount is applied as a bind
mount on Docker daemons. (The `--mount` help text mentions a manifest declaration; no such gate exists in the
code today.) On **Kubernetes**, mounts are recorded with the deploy options but **not** mounted into pods;
persistent data goes through the PVC/`APP_PERSISTENT_STORAGE` instead. Mounts are replayed on `app:update`.

## Kubernetes service roles

For multi-process ExApps on Kubernetes, each `<role>` becomes its **own Deployment** (same image, plus the
role's `<env>` line). Only roles with `<expose>true</expose>` get a Kubernetes Service, and the **first**
exposed role is the app's single entry point for HaRP routing; other roles are internal-only. An ExApp without
roles gets one default Deployment. On Docker daemons the roles element is ignored.

## Minimal working example (JSON form)

The shape used by app_api's own Kubernetes tests, as an `--json-info` payload:

```json
{
    "id": "app-skeleton-python",
    "name": "App Skeleton Python",
    "version": "1.0.0",
    "docker-install": {
        "registry": "ghcr.io",
        "image": "nextcloud/app-skeleton-python",
        "image-tag": "latest"
    },
    "k8s-service-roles": [
        {"name": "api", "env": "SERVICE_ROLE=api", "expose": true},
        {"name": "worker", "env": "SERVICE_ROLE=worker", "expose": false}
    ]
}
```

A complete XML reference manifest lives in the `nextcloud/test-deploy` repository (the app the admin UI's
**Test deploy** button installs). A minimal working XML instance is
[minimal_exapp/appinfo/info.xml](../assets/minimal_exapp/appinfo/info.xml). For building the ExApp itself, see
**nc_py_api** (https://github.com/cloud-py-api/nc_py_api).
