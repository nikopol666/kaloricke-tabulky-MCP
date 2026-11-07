# Changelog

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

Phases 2 and 3 are not included in this release.
