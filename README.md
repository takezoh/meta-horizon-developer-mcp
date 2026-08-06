# meta-horizon-developer-mcp

A **read-only** stdio MCP server for collecting **Analytics** from the Meta Horizon
developer dashboard (`developers.meta.com/horizon`).

The MCP server is the integration entry point for other projects. The bundled
`meta-horizon-developer` CLI is for the **collection owner** only (manual authentication
and scheduled collection).

The collector opens the Analytics page in an authenticated browser, observes only
successful persisted GraphQL `Query` responses issued by the Dashboard itself, removes
secrets, and stores immutable JSON. It never constructs and replays requests, performs
mutations, or fetches arbitrary URLs.

The caller chooses the target application (`app_id`) and storage locations. Downstream
handling of collected values (such as DuckDB registration) is outside this package's
responsibility.

## Usage

```bash
# 1. Log in manually (cookies remain in the dedicated Chromium profile).
meta-horizon-developer --app-id <APP_ID> --profile-dir .state/meta-horizon-developer/profile auth

# 2. Collect data (cron-friendly; writes one immutable JSON file).
meta-horizon-developer --app-id <APP_ID> --snapshot-dir data/raw/meta-store collect

# 3. Check status.
meta-horizon-developer --app-id <APP_ID> --snapshot-dir data/raw/meta-store status
```

Arguments can also be supplied through environment variables.

| Environment variable | Corresponding argument |
|---|---|
| `META_HORIZON_APP_ID` | `--app-id` |
| `META_HORIZON_OUTPUT_DIR` | `--snapshot-dir` |
| `META_HORIZON_PROFILE_DIR` | `--profile-dir` |
| `META_HORIZON_CSV_DIR` | `--csv-dir` |
| `META_HORIZON_LOOKBACK_DAYS` | `--lookback-days` (default: 120) |
| `META_HORIZON_STATE_DIR` | Parent of the three paths above (default: `./.state/meta-horizon-developer`) |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE` | Chromium executable |

## MCP

Start it as a stdio MCP server. Register it according to the host's conventions (through
the gateway or directly in the host's MCP configuration). Only the executable path,
`--app-id`, and `--snapshot-dir` are required.

```json
{
  "mcpServers": {
    "meta-horizon-developer": {
      "command": "/path/to/.venv/bin/meta-horizon-developer-mcp",
      "args": ["--app-id", "<APP_ID>", "--snapshot-dir", "/path/to/data/raw/meta-store"]
    }
  }
}
```

Only four tools are exposed:

- `meta_horizon_developer_status` — authentication status and latest snapshot health
- `meta_horizon_developer_collect` — run a collection
- `meta_horizon_developer_latest_snapshot` — latest snapshot metadata (not its payload)
- `meta_horizon_developer_read_operation` — read one allowed Query

**Authentication, arbitrary URLs, arbitrary operations, mutations, and Cookie/token values
are never exposed.** Authentication is available only through the CLI because it requires
human interaction.

## Invariants

- **One process per profile.** `auth` / `collect` / `capture-csv` take an exclusive lock and
  fail immediately with `ProfileBusyError` if it cannot be acquired.
- **One application per directory.** Never write snapshots for a different `app_id` into
  a directory that already contains snapshots (`AppIdMismatchError`).
- **Always pass `--password-store=basic`.** Without a keyring, Chromium may select another
  key and appear unauthenticated because it cannot decrypt any saved cookies.
- **Separate proxy credentials.** Chromium does not use credentials embedded in a proxy URL
  for CONNECT; passing them that way results in 407 and `ERR_TUNNEL_CONNECTION_FAILED`.
- **The aggregation period comes from URL `start` / `end`.** Presets allow at most 28 days,
  so a custom period (default: 120 days) is passed to the Dashboard to produce a longer
  daily series.
- **Fail when collection captures zero operations.** Do not leave an empty snapshot. Partial
  gaps are recorded in `missing_operations` / `missing_required_operations`.

## Snapshot format

```json
{
  "schema_version": 1,
  "collected_at": "2026-08-06T01:23:45Z",
  "source": "meta-horizon-dashboard",
  "app_id": "…",
  "operations": [{"name": "…Query", "doc_id": "…", "data": {…}, "errors": null}],
  "missing_operations": ["…"],
  "missing_required_operations": ["…"]
}
```

Files are named `meta-store-<UTC>.json`, mode 0600, and are **never overwritten**.

`dashboard.json` is the contract for the Dashboard pages and allowed operations; it does
not identify an application. The page layout is shared across applications, so this
allowlist is shared as well.

## Secrets

- Cookies remain only in the dedicated profile; they are not written to configuration or snapshots.
- Responses are redacted before storage (authorization / cookie / token / session / email /
  user_id / dtsg and similar fields).
- Profiles and snapshots must never be committed to Git.
