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

Phases 2 and 3 are not included in this release.
