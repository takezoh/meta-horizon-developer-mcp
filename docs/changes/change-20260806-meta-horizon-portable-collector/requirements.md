---
change: change-20260806-meta-horizon-portable-collector
role: requirements
---

<!-- lifecycle is owned by change.md -->

# Requirements

## Required behavior

### FR-001 — The target app is determined at runtime

When given an `app_id` via argument or environment variable, the collection
engine must operate against that app's Dashboard. The allowlist
(`dashboard.json`) must hold only the Dashboard's screen layout and
operations, and **must not include which app it applies to**. If `app_id` is
supplied by neither source, collection must not start and must fail as a
configuration error.

### FR-002 — Self-contained, independent of the caller

The collection engine must not depend on DuckDB, pandas, or any specific
repository's directory structure. Its dependencies are limited to playwright
and mcp.

### FR-003 — No default storage location

The storage locations (snapshot / profile) must always be supplied via
argument or environment variable, and **must not have a default rooted at
the working directory**. Since the MCP server is started by the host with an
unpredictable cwd, having a default would mean the profile ends up in a
different location depending on the launcher, producing the failure mode of
being redirected to a login screen despite already being authenticated. Only
the CSV storage location may default to next to the profile (since it is
only used for recovery/verification).

### FR-004 — At most one process holds the profile at a time

`auth` / `collect` / `capture-csv` must acquire an exclusive lock on the
browser profile before launching the browser. If another process already
holds it, they must fail **immediately, without waiting**, with
`ProfileBusyError`.

### FR-005 — One app per directory

If a snapshot for a different `app_id` already exists at the storage
location, `collect` must fail with `AppIdMismatchError` **without writing a
single byte**.

### FR-006 — MCP's exposed surface

The MCP server must take the target app and storage location via startup
arguments, and must expose only the 4 tools corresponding to `status` /
`collect` / `latest_snapshot` / `read_operation`. It must not expose
authentication, fetching arbitrary URLs, operations outside the allowlist,
Mutations, or cookie/token retrieval. This package must not decide how it is
registered with hosts or under what public name.

### FR-007 — The snapshot format does not change

Snapshots captured before the extraction must remain readable as-is. The
file name (`meta-store-<UTC>.json`), permissions (0600), `schema_version`,
`source`, and the structure of `operations` / `missing_operations` /
`missing_required_operations` must not change.

### FR-008 — Operational failures return in a readable form

The CLI must return profile double-launch, wrong-app directory, and
zero-count collection as a single-line message with a non-zero exit code,
not as a traceback.

## Counterexamples (must not be satisfied)

- Duplicating `dashboard.json` to watch a different app
- `collect` writing next to a different app's snapshot
- Leaving behind an empty snapshot on zero-count collection
- Being able to reach `auth` or an arbitrary operation from MCP
- The collection engine assuming a warehouse naming scheme or the presence
  of DuckDB
- Creating a profile in the launching working directory when the storage
  location is unspecified
- The profile becoming openable concurrently if the caller's cron drops
  `flock`
