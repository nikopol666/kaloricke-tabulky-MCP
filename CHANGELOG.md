# Changelog

## 0.3.0

Phases 2 and 3, first release from this fork
(`ghcr.io/michalprouza/kaloricke-tabulky-mcp`).

Diary reads (phase 2):

- Add `get_diary_range` with inclusive 92-day validation, bounded parallel reads,
  standard nutrient units, explicit missing values and averages over logged days.
  Weigh-ins are matched to days also by KT's `DD.MM.YY` labels.
- Add validated `KT_MAX_CONCURRENCY` (default 4) and Compose forwarding.
- Add `get_diary_entries`: compact entries grouped by meal with meal and day
  totals, read from the live diary shape (entry nutrient totals, kJ → kcal,
  quantity in g/ml parsed from the unit text). Verified against a live diary;
  tests use an anonymized excerpt.
- Add membership-checked deletion previews. Actual deletion stays disabled until
  its method, endpoint and body are captured from the KT web application.

Authentication and robustness (phase 3):

- Coalesce concurrent authentication/refresh operations with a lock and session
  generation counter. Retry expired requests once for redirects, HTTP 401/403,
  and a conservative login-message allowlist; unrelated errors do not relogin.
- Bound API errors to 300 characters and redact credentials from both user
  errors and full DEBUG diagnostics. Release HTTP responses on failure paths.
- Resolve batch/recipe ingredients concurrently using the same semaphore as
  range reads; preserve input/result order and sequential diary writes.
- Gate raw/probe MCP tools (including `get_diary_detail`) behind
  `KT_ENABLE_DIAGNOSTICS=false`, correct the outdated recipe-write diagnostic,
  and revise all MCP tool descriptions.
- Exact live KT JSON expiry wording remains unverified.

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
- Read the live diary shape (`times[].foodstuff[]`, `guidType`, textual `unit`)
  for duplicate checks; compare portions by unit label and count or by total
  g/ml, recipe servings by total weight.
- Require `meal_type` or `time` when the date is not today.
- Fix unit selection: `amount` without a unit is grams/millilitres (`63` was
  recorded as 63 x the form's default unit, e.g. 63 x 50 g); count units such as
  `kus` select their option; unavailable or ambiguous units are errors.
- Never auto-select a search result without an exact title match, even a single
  one; ambiguous batch items and recipe ingredients report `status: "ambiguous"`
  in preview.
- Add mocked regression tests, local HTTP integration tests and an anonymized
  live diary fixture. Live write verification remains pending.

Phase 1 changes are kept separately from later work in Git history.
