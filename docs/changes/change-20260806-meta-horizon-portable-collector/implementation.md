---
change: change-20260806-meta-horizon-portable-collector
role: implementation
---

<!-- lifecycle is owned by change.md -->

# Implementation

## Structure

```
bin/meta-horizon-developer          owner CLI launcher
bin/meta-horizon-developer-mcp      MCP launcher
src/meta_horizon_developer/
  core.py                           collection engine (config resolution / browser /
                                     interception / redact / lock / app_id guard /
                                     snapshot reading)
  cli.py                            auth / collect / capture-csv / read / status / migrate-profile
  mcp_server.py                     the 4 read-only tools, bound to a single Settings
  dashboard.json                    Dashboard contract (8 pages / 59 operations, no app_id)
tests/test_core.py
```

The launchers resolve their own location by following the symlink from
`~/.local/bin` and start the module with `uv run --project`. **The caller
only needs to know `${HOME}/.local/bin/…`; moving the repository only
requires re-pointing the symlink, not changing any configuration.**

## Where the boundary is drawn

**"Up to writing the immutable JSON" is this package; "reading it and
loading it" is the caller's.**

Registering collection results into DuckDB's staging VIEWs or aggregate
tables has not been moved here. That is the responsibility of whoever knows
the warehouse's naming conventions and data-layer design, so that projects
which only want to collect are not forced to depend on DuckDB.

## What changed in the extraction

| Aspect | Before extraction | Now |
|---|---|---|
| Allowlist | endpoints JSON bundled with `app_id` | `dashboard.json` (no app_id, `schema_version: 2`) |
| Config resolution | storage location fixed relative to the caller repository's root | `app_id` / `snapshot_dir` / `profile_dir` are required inputs. No default rooted at cwd |
| Exclusion | only the cron script's `flock` | `core.profile_lock()` (non-blocking). Cron's `flock` remains as a secondary guard against redundant launches |
| app_id contamination | unguarded | `assert_app_id_matches()` rejects it at the start of `collect` |
| Empty collection | wrote and registered a snapshot, then deleted it and raised `RuntimeError` | `EmptyCollectionError`. **Leaves not a single byte behind** |
| MCP | fixed to one app via a module constant | bound via `build_server(settings)`. Switchable via startup arguments |
| Exceptions | `RuntimeError` / `ValueError` | under `MetaHorizonError` (`ProfileBusyError` / `AppIdMismatchError` / `EmptyCollectionError`). All still inherit from `RuntimeError`, so existing catches keep working |

## Caller-side (data-analysis) adaptation

Outside this package's scope, but made alongside the extraction.

- Repointed the collection engine's import to this package, and turned the
  caller into a thin wrapper that injects the default paths
  (`data/raw/meta-store/`, `.state/meta-horizon/`)
- Left `register_snapshot()` (the staging VIEW and the collection-health
  aggregate table) on that side
- **Existing snapshots have not been moved.** Since the storage location can
  now be passed as an argument, snapshots captured before the extraction can
  still be read as-is
