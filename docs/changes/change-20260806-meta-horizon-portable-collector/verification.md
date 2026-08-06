---
change: change-20260806-meta-horizon-portable-collector
role: verification
---

<!-- lifecycle is owned by change.md -->

# Verification

## Status (as of 2026-08-06)

Verification was performed **as a workspace member of the source repository
(data-analysis)**. It has not been re-run in the post-move repository on its
own (UD-001 in change.md).

| Gate | Result |
|---|---|
| `uv run python -m pytest` | **923 passed** (0 failures; 23 of these are this package's tests) |
| `uv run ruff check` | **All checks passed** (same result standalone as a package) |
| `uv sync` | resolved and installed as a workspace member |

## Test breakdown (`tests/test_core.py`, 23 cases)

None connect to a real browser or real data.

- The allowlist is `*Query` only, has 8 sections, and `required ⊆ operations`
- **`dashboard.json` has no `app_id`** (directly pins the counterexample for FR-001)
- Fails when `app_id` is unspecified / falls back to env / argument takes
  precedence over env
- **Fails when `--snapshot-dir` and `--profile-dir` are unspecified** (no
  default rooted at cwd)
- `--csv-dir` defaults to next to the profile
- Allowlist validation (rejects non-Query, rejects required outside the set)
- `page_url` carries the custom period (120 days) and app_id
- `redact` and `proxy_settings` (3 forms)
- **`profile_lock` rejects a second holder and can be reacquired after release**
- **`assert_app_id_matches` rejects a different app_id / passes on same or empty**
- `read_operation` rejects operations outside the allowlist and reads correctly otherwise
- `status` reports app_id and the storage location

## Runtime checks

All of these were **snapshot reads only**; no requests were made to Meta.

| Check | Result |
|---|---|
| `meta-horizon-developer --help` | shows the 6 actions and common arguments |
| `meta-horizon-developer --app-id <other-id> --snapshot-dir <tmp> status` | **starts as a different app** and correctly reports an empty storage location (no allowlist duplication) |
| Running `assert_app_id_matches` with a different app_id against an existing storage location | `AppIdMismatchError: … holds snapshots for app_id=…; refusing to write app_id=…` |
| MCP tool listing | **exactly 4**: `meta_horizon_developer_status` / `_collect` / `_latest_snapshot` / `_read_operation` |
| Running from outside the repository via the `~/.local/bin` symlink | starts correctly. **This check surfaced the cwd-rooted default problem, which led to making the arguments required** |

## Unverified (remaining risk)

- **A real-browser `collect` has not been run.** Collection that launches a
  browser (`auth` / `collect` / `capture-csv`) has not been exercised, so the
  Playwright path's regression check **will happen for the first time on the
  next scheduled run**. The parts of that path touched by the extraction are
  the addition of `profile_lock` and the insertion of
  `assert_app_id_matches`, both of which complete before the browser
  launches. If they fail, no new snapshot is added to the storage location,
  so the caller's freshness check will catch it.
- **pytest / ruff have not been run standalone in the post-move
  repository** (UD-001).
- **The README's environment variable table still lists
  `META_HORIZON_STATE_DIR`.** That variable was removed from the
  implementation when the cwd-rooted default was dropped, so the table and
  the implementation are now out of sync.
