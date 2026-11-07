# Changelog

## Unreleased — phase 3

- Coalesce concurrent authentication/refresh operations with a lock and session
  generation counter. Retry expired requests once for redirects, HTTP 401/403,
  and a conservative login-message allowlist; unrelated errors do not relogin.
- Bound API errors to 300 characters and redact credentials from both user
  errors and full DEBUG diagnostics. Release HTTP responses on failure paths.
- Resolve batch/recipe ingredients concurrently using the same semaphore as
  range reads; preserve input/result order and sequential diary writes.
- Gate raw/probe MCP tools behind `KT_ENABLE_DIAGNOSTICS=false`, correct the
  outdated recipe-write diagnostic, and revise all MCP tool descriptions.
- Exact live KT JSON expiry wording remains unverified. The phase 2 live diary
  mapping and deletion endpoint blockers remain unchanged.

## Unreleased — phase 2 draft

- Add `get_diary_range` with inclusive 92-day validation, bounded parallel reads,
  standard nutrient units, explicit missing values and averages over logged days.
- Add validated `KT_MAX_CONCURRENCY` (default 4) and Compose forwarding.
- Develop a compact diary adapter and grouped totals using a clearly synthetic
  fixture. A real anonymized KT response is still required to finish phase 2.2.
- Add membership-checked deletion previews. Actual deletion remains disabled
  until its method, endpoint and body are verified; phase 2.3 is not complete.

## 0.2.0

Phase 1: prevent silent diary data errors.

- Infer omitted meal types from local time and expose `meal_type_source`.
- Add validated `KT_TIMEZONE` (default `Europe/Prague`) and timezone-aware date
  defaults throughout the client, tools and input models; include `tzdata`.
- Rank food/recipe matches and block ambiguous writes with candidate details;
  allow explicit GUID selection or opt-in `allow_fuzzy`.
- Skip duplicate food/recipe servings by comparing a fresh diary snapshot before
  writing; share one snapshot per batch, including entries written in that batch.
  Add opt-in `allow_duplicates` and serialize writes within each client.
- Add mocked regression tests and local HTTP integration tests. Live test-account
  verification and confirmation of diary payload mappings remain pending.

Phase 1 changes are kept separately from later work in Git history.
