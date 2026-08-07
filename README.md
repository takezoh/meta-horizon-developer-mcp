# meta-horizon-developer-mcp

A **read-only** stdio MCP server for collecting **Analytics** from the Meta Horizon
developer dashboard (`developers.meta.com/horizon`).

For package structure and trust boundaries, see [`ARCHITECTURE.md`](ARCHITECTURE.md). For
development and change rules, see [`AGENTS.md`](AGENTS.md).

The bundled `meta-horizon-developer` CLI is for the **collection owner** only (manual
authentication and scheduled collection).

The collector opens the Analytics and Reviews pages in an authenticated browser, observes
only successful persisted GraphQL `Query` responses issued by the Dashboard itself, removes
secrets, and stores immutable JSON. It never constructs and replays requests, performs
mutations, or fetches arbitrary URLs.

## Usage

```bash
# 1. Log in manually (cookies remain in the dedicated Chromium profile).
meta-horizon-developer --app-id <APP_ID> --profile-dir .state/meta-horizon-developer/profile auth

# 2. Collect data (cron-friendly; writes one immutable JSON file).
meta-horizon-developer --app-id <APP_ID> --snapshot-dir data/raw/meta-store collect

# 3. Check status.
meta-horizon-developer --app-id <APP_ID> --snapshot-dir data/raw/meta-store status
```

CLI arguments can also be supplied through environment variables.

| Environment variable | Corresponding argument | Needed for |
|---|---|---|
| `META_HORIZON_APP_ID` | `--app-id` | everything |
| `META_HORIZON_OUTPUT_DIR` | `--snapshot-dir` | everything |
| `META_HORIZON_PROFILE_DIR` | `--profile-dir` | `auth` / `collect` / `capture-csv` |
| `META_HORIZON_CSV_DIR` | `--csv-dir` | `capture-csv` (defaults next to the profile) |
| `META_HORIZON_LOOKBACK_DAYS` | `--lookback-days` (default: 120) | `collect` |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE` | Chromium executable | browser operations |

Reading a snapshot never needs a profile: `status` and `read` resolve without one.

## MCP

Start it as a stdio MCP server. **It takes no startup arguments.** The target
application and storage location are tool arguments, so one process serves every
application and the host that launches it needs no knowledge of its callers.

```json
{
  "mcpServers": {
    "meta-horizon-developer": {
      "command": "/path/to/bin/meta-horizon-developer-mcp"
    }
  }
}
```

Splitting the server per application would isolate nothing: every instance opens the
same Chromium profile and the same cookies, so the only thing separated is `app_id`,
which is not a secret.

Only four tools are exposed:

| Tool | Arguments | Returns |
|---|---|---|
| `meta_horizon_developer_status` | `app_id`, `snapshot_dir` | latest snapshot health |
| `meta_horizon_developer_collect` | `app_id`, `snapshot_dir`, `profile_dir`, `lookback_days?` | runs a collection |
| `meta_horizon_developer_latest_snapshot` | `app_id`, `snapshot_dir` | snapshot metadata (not its payload) |
| `meta_horizon_developer_read_operation` | `operation_name`, `app_id`, `snapshot_dir` | one allowed Query |

User reviews need no separate tool: read them with `meta_horizon_developer_read_operation`
using `OCDevManageApplicationReviewsTableQuery`.

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
- **Finish reading a response before leaving its page.** Chromium discards the body once
  the page is gone, and a handler that has not read it yet loses that operation silently.
- **Reviews are read by scrolling, and their authors are dropped before storage.** The
  reviews table has no pager and no period parameter: it extends its GraphQL connection
  as the inner container is scrolled, so the collector scrolls until `has_next_page` is
  false. Every row also carries the reviewer's alias and account id, which name a person
  and which no secret-key pattern would match, so they are removed at capture time rather
  than redacted afterwards.

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

A scrolled table is many responses merged into one operation, so it is stored as a flat
list instead of a GraphQL connection. `truncated` records that the Dashboard still had
more rows when the round limit stopped the scroll — without it a short list would read as
"these are all the reviews".

```json
{"name": "OCDevManageApplicationReviewsTableQuery", "doc_id": "…", "errors": null,
 "data": {"rows": [{"id": "…", "date": 1786083632, "score": 5, "title": "…",
                    "description": "…", "helpful_count": 1, "is_down_ranked": false,
                    "developer_tags": [], "app_version": "0.1.10283",
                    "developer_response": "…", "has_moderation_request": false}],
          "truncated": false, "response_count": 20}}
```

`dashboard.json` is the contract for the Dashboard pages and allowed operations; it does
not identify an application. The page layout is shared across applications, so this
allowlist is shared as well. `pages` are opened with the analytics period; `scroll_pages`
carry no period and are read by scrolling to the end of their table.

## Secrets

- Cookies remain only in the dedicated profile; they are not written to configuration or snapshots.
- Responses are redacted before storage (authorization / cookie / token / session / email /
  user_id / dtsg and similar fields).
- Review authors are dropped at capture time. Redaction matches key names, and the author
  arrives under `alias` / `id`, which no pattern would catch.
- Profiles and snapshots must never be committed to Git.

## Development

```bash
uv run --project . pytest -q
uv run --project . ruff check src tests
```

The package requires Python 3.13 or newer. Runtime state is intentionally excluded from Git.
