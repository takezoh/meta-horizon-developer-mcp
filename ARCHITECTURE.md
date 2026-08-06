# Architecture

## System boundary

`meta-horizon-developer-mcp` is a local collector for the Meta Horizon developer Dashboard.
It uses an authenticated Chromium profile and Playwright to observe Dashboard traffic, then
stores a redacted, immutable snapshot for later reads.

The package has no service-side API client. The Dashboard remains responsible for login,
request construction, and persisted GraphQL operation selection.

## Components

```text
bin/meta-horizon-developer          owner CLI wrapper
bin/meta-horizon-developer-mcp      stdio MCP wrapper
        │
        ▼
src/meta_horizon_developer/
  cli.py                             auth / collect / capture-csv / read / status
  mcp_server.py                      four read-only MCP tools
  core.py                            settings, browser, capture, redaction, snapshots
  dashboard.json                     pages, allowed Queries, required Queries
```

Both wrappers resolve the package directory and invoke the Python module with `uv`. The
runtime code is shared; the CLI owns human interaction, while the MCP facade takes the
target application and storage location **per call** and exposes only read operations.
A single process therefore serves every application: binding one server per application
would separate only `app_id`, since all instances share one Chromium profile and its
cookies.

## Data flow

1. `load_settings()` resolves `app_id`, snapshot, profile, CSV, and lookback values from
   explicit arguments or environment variables, and validates `dashboard.json`. `app_id`
   and the snapshot directory are always required; the profile is required only by the
   browser entry points, so reads resolve without one.
2. `profile_lock()` creates a 0600 lock beside the profile and rejects concurrent use.
3. Playwright opens the persistent profile with `--password-store=basic` and the optional
   proxy settings.
4. `collect()` navigates to the allow-listed Dashboard pages. Response handlers accept only
   successful POST responses to the known GraphQL endpoint whose operation is allow-listed.
5. `redact()` removes credential and identity fields, bounds oversized values, and the
   collector writes one timestamped 0600 snapshot. Empty collections are deleted and fail.
6. `status()` and `read_operation()` read snapshot metadata or one allow-listed operation.

`auth` and `capture-csv` use the same profile lock but are intentionally CLI-only because
they require a human browser session or a download interaction.

## Trust boundaries

| Boundary | Rule |
|---|---|
| Dashboard → collector | Observe successful allow-listed responses; never replay or synthesize requests. |
| Browser profile → filesystem | Keep cookies in the dedicated profile; use basic password storage and mode 0700 directories. |
| Response → snapshot/MCP | Redact secret and identity keys before persistence or return. |
| Caller → settings | Require an explicit application and snapshot directory per call; reject cross-application snapshot directories. |
| Snapshot → reader | Read only known operations from the latest redacted snapshot; never expose the whole raw payload through MCP. |

## Extension rules

- Add a Dashboard page or Query only through `dashboard.json`, with tests proving it is a
  read-only Query and that required-operation semantics remain valid.
- Add a public MCP tool only when its input and output preserve the read-only boundary. Update
  the tool contract tests and README at the same time.
- Keep browser-specific behavior in `core.py`; do not duplicate profile, proxy, lock, or
  redaction logic in CLI/MCP adapters.
- Keep runtime data outside Git. The package `.gitignore` excludes state, data, caches, and
  build artifacts.

## Non-goals

- No mutation or arbitrary GraphQL/API client.
- No credential manager, token export, or unattended login.
- No host-specific MCP registration logic inside this package.
