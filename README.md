# Kaloricke Tabulky MCP

MCP server for searching and writing Kaloricke Tabulky food diary entries through the unofficial web endpoints used by the Kaloricke Tabulky web app.

This is a fork of [nikopol666/kaloricke-tabulky-MCP](https://github.com/nikopol666/kaloricke-tabulky-MCP) with diary data safety, diary range/entry reads and hardened authentication (see `CHANGELOG.md`). Images are published to `ghcr.io/michalprouza/kaloricke-tabulky-mcp`.

The server is designed for assistant workflows such as "record these Mealie-derived foods into Kaloricke Tabulky" without coupling v1 directly to Mealie. It accepts normalized item payloads from any caller.

## Safety Model

- Multiple accounts are configured through environment variables.
- Read/search tools may use `KT_DEFAULT_ACCOUNT`.
- Every write tool requires an explicit `account`.
- Write tools never write by default.
- Actual writes happen only when `commit=true`.
- Streamable HTTP `/mcp` can be protected with `MCP_BEARER_TOKEN`.
- `/health` checks process/config liveness only. It does not log in, search, or write.
- Active credential validation is exposed as the explicit `check_account_auth(account)` tool.

## Docker

```bash
cp .env.example .env
docker compose up -d
curl -fsS http://localhost:8080/health
```

Image:

```text
ghcr.io/michalprouza/kaloricke-tabulky-mcp:latest
```

`latest` is published only from semver release tags.

## Environment

```env
KT_ACCOUNTS=personal,partner
KT_ACCOUNT_PERSONAL_EMAIL=user@example.com
KT_ACCOUNT_PERSONAL_PASSWORD=replace-me
# Optional alternative when plaintext contains characters that are hard to pass through Compose/Portainer:
# KT_ACCOUNT_PERSONAL_PASSWORD_MD5=32-character-browser-md5
KT_ACCOUNT_PARTNER_EMAIL=partner@example.com
KT_ACCOUNT_PARTNER_PASSWORD=replace-me
# KT_ACCOUNT_PARTNER_PASSWORD_MD5=32-character-browser-md5
KT_DEFAULT_ACCOUNT=personal
KT_TIMEZONE=Europe/Prague
KT_MAX_CONCURRENCY=4
KT_ENABLE_DIAGNOSTICS=false
MCP_BEARER_TOKEN=replace-me-long-random-token
MCP_HTTP_PORT=8080
```

Aliases are normalized to lowercase underscores. Collisions such as `my-account` and `my_account` are rejected.
`KT_ACCOUNT_<ALIAS>_PASSWORD_MD5` is optional and takes precedence over plaintext password when set. Treat it as password-equivalent secret material.
When `MCP_BEARER_TOKEN` is set, MCP clients must send `Authorization: Bearer <token>` to `/mcp`. `/health` stays unauthenticated for container healthchecks.

`KT_TIMEZONE` is an IANA timezone (default `Europe/Prague`). Invalid names stop
startup with a configuration error. Omitted dates use the current date in this
timezone, including around midnight and daylight saving transitions. Explicit
dates accept `YYYY-MM-DD` or `DD.MM.YYYY`. Python's `zoneinfo` uses system timezone
data, with the `tzdata` dependency as a fallback for slim images.

### Diary write defaults and safeguards (0.2.0)

`record_food`, `record_food_batch` and `record_recipe_serving` retain preview as
the default; writes require an explicit `account` and `commit=true`.

- `meal_type` accepts `breakfast`, `morning_snack`, `lunch`, `afternoon_snack`,
  `dinner`, `second_dinner`, or IDs `1`–`6`. It takes precedence over `time`.
  With no explicit meal, `time` (`HH:MM`) selects the meal; with neither, the
  current time in `KT_TIMEZONE` selects it. Inferring from the current time is
  allowed only for today: a different `date` without `meal_type` or `time` is
  rejected before any KT request. Preview includes `meal_type_source`:
  `explicit`, `time`, or `now`. Batch item values take precedence over batch
  defaults. Meal boundaries are 05:00, 10:00, 11:30, 14:30, 17:30 and 21:30;
  times before 05:00 use `second_dinner`.
- `amount` without `unit` (or with `g`, `kg`, `ml`, `l`) is a quantity in grams
  or millilitres: a unit option of exactly that size is used once (`42` →
  `kus (42 g)` x 1), otherwise the `1 g`/`1 ml` option `amount` times (`63` →
  `1 g` x 63). Any other `unit` names an option and `amount` counts it
  (`unit: "kus", amount: 1.5` → `kus (42 g)` x 1.5). A unit the food does not
  offer, or one matching several options, is an error listing the available
  units; it never falls back to the form's default unit. `unit_guid` selects an
  option directly and `amount` then counts it.
- Search candidates are ranked by exact normalized title (case/diacritics and
  whitespace ignored), then favorite, then title starting with the query.
  Without an exact title match the lookup is always ambiguous, even with a
  single result or a favorite. Tied exact matches are also ambiguous unless the
  ranking distinguishes them. Responses include `ambiguous: true` and up to
  five ranked `candidates` with `food_guid`, `title`, `brand_name`, `energy` and
  `unit`. A preview reports `status: "ambiguous"` (also for batch items and
  recipe ingredients); a commit reports an error and makes no write. Select a candidate with `food_guid` (or `recipe_guid` for a
  recipe), or explicitly opt into the highest ranked result with
  `allow_fuzzy=true`. The override also works on batches, recipe import/create
  requests, and individual ingredients/items. An ambiguous batch item prevents
  the entire batch from writing.
- Before committing, read the target day's diary and compare food/recipe GUID,
  meal and portion. A match returns `status: "skipped_duplicate"` with
  `existing_record` (`entry_guid`, `title`, `time`, `unit`, parsed `portion`);
  it does not make a write request. The diary shows portions only as text such
  as `1,5 x kus (42 g)`, so the same unit label is compared by count, and
  different labels by total quantity in g or ml: `1,5 x kus (42 g)` and
  `63 x 1 g` are the same portion, g and ml are never equated, and a label
  without a quantity (`lžíce`) only matches the same label. A recipe serving is
  shown in the diary as its total weight (`306,23 x 1 g`) and is compared by
  the weight of its scaled ingredients. Use `allow_duplicates=true` to
  intentionally repeat a portion and bypass the diary lookup. Preview performs
  no duplicate lookup.
- A batch reads the diary once per commit attempt and adds each successful
  write to that snapshot, so repeated items within the same batch are also
  skipped. Commit results include `written_count` and `skipped_duplicate_count`;
  `success_count` includes both written and skipped items. If KT omits the new
  entry ID, a later duplicate in the same batch references `written_in_request`
  and `write_index` instead. Calls sharing a client serialize diary writes.
  Independent server instances/external clients can still race: this is a
  read-before-write guard, not server-side transactional idempotency.

Duplicate normalization reads the live diary shape (see `diary.py`):
`data.times[]` with the meal in `id` (`1`–`6`) and rows in `foodstuff[]`, where
`id` is the diary entry, `guidType` the food or custom recipe GUID and `unit`
the displayed portion; `activities`, `notes` and totals are ignored. Unknown or
incomplete structures and diary read failures **block writes** when the guard
is enabled. The mapping was checked against a live diary payload and live add
forms (unit titles such as `kus (42 g)` match the diary text); the tests use an
anonymized excerpt and a local fake KT HTTP server and make no real KT requests.
Still unverified with a real write: the ID KT returns for a new entry (only
used for references within a batch) and the recipe serving weight.

## Tools

### Robustness and diagnostics (phase 3)

Concurrent requests on one client share a locked authentication refresh. A
session generation counter prevents late failures from triggering another
login after another request already refreshed the session. Requests retry once
after a redirect, HTTP 401/403, or a nonzero API code with a recognized login
message. A second authentication failure is returned without another retry.
Unrelated business errors do not trigger login.

The JSON login-message allowlist is conservative and tested with synthetic
English/Czech messages. **The exact live KT expiry message still needs a captured
response to complete that verification of phase 3.2.** HTTP status handling and
concurrent login behavior are covered by a local fake HTTP server.

API error text is limited to 300 characters and includes the response type.
Sanitized full error bodies are available only at DEBUG level. Passwords,
password-equivalent hashes, known session cookie values and named credential
fields are redacted before both error rendering and DEBUG logging.

Food and ingredient resolution in batches, recipe preparation and recipe
creation runs concurrently under the same per-client `KT_MAX_CONCURRENCY`
semaphore as daily range reads. Results retain input order; diary writes remain
sequential. `continue_on_error=false` stops reported results at the first input
error and stops subsequent writes after a write error. Read lookups already
started concurrently may finish; any resolution error prevents all writes.

`KT_ENABLE_DIAGNOSTICS` defaults to `false`. Only when enabled are
`probe_user_meal_endpoints` and the token-heavy raw `get_diary_detail` registered
as MCP tools. Their internal client methods remain usable by normal diary and
duplicate-check workflows. Health, compact diary and range tools remain
available with diagnostics off.

### `get_diary_range(account, date_from, date_to)`

Read an inclusive range of up to 92 days (`YYYY-MM-DD`). Returns ordered daily
rows with `logged`, energy in kcal, protein/carbohydrate/fat/fiber/sugar/salt in g,
an energy target and weight in kg when available. Missing values remain `null`.
Energy in kJ and nutrient amounts in mg are converted to the advertised units.
Weight records are used only when explicitly associated with the requested date.

The summary includes `days_with_record`, `empty_days`, `averages` and per-metric
`average_sample_counts`. A day is logged if its detail contains diary entries or
its nutrition summary contains positive intake; goals/weight alone do not count.
Empty days are excluded from averages. Missing metrics on logged days are also
excluded from that metric's average. Read failures fail the range rather than
silently becoming empty days. `KT_MAX_CONCURRENCY` (default 4, range 1–32) limits
parallel daily reads per account/client.

### `get_diary_entries(account, date=None)`

Returns the day's entries in compact form: `entry_guid`, `food_guid`,
`is_recipe`, `meal_type`, `time`, `title`, `unit` (KT's display text such as
`1,5 x kus (42 g)`), `quantity` with `quantity_unit` (`g`/`ml`, or `null` when
the unit states no weight), `energy_kcal`, `protein_g`, `carbohydrate_g`,
`fat_g`, `fiber_g`, `sugar_g` and `salt_g`. Entries are also grouped into
non-empty `meals` (with the meal `title`) with meal and day `totals`.

KT reports nutrients per diary entry as totals for the whole portion; energy in
kJ is converted to kcal. A missing value (`null` in KT) makes the corresponding
total `null` rather than an understated sum. The mapping was verified against a
live diary: entry sums match KT's daily summary. Unknown structures fail with an
error. `get_diary_detail` remains the raw diagnostic response.

### `delete_diary_entry(account, date, entry_guid, commit=false)` (preview only)

Checks that the entry exists in the requested day and previews its title,
quantity and meal. Preview returns `write_supported: false`.

**Deletion is blocked pending an authenticated browser capture of the deletion
request.** The endpoint, HTTP method and payload are not known and no deletion
endpoint has been invented. `commit=true` currently returns an error and makes
no deletion request. To finish this feature, record the actual method, URL and
body in DevTools on a test account, excluding cookies/credentials, document it
here, and add mocked endpoint tests before enabling writes.

### `list_accounts`

Lists safe account aliases and masked email addresses.

### `check_account_auth(account)`

Runs an explicit login check for one configured account.

### `probe_user_meal_endpoints(account)`

Read-only diagnostic for discovering the private custom meal/recipe creation
surface from an authenticated KT account. It fetches only page/form URLs such as
`/user/recipe/add`, `/user/foodstuff-meal/create`, and
`/user/settings/meal/detail/0`, then returns form actions, input names, script
URLs, and endpoint-like strings. It does not call `add`/`save` JSON endpoints and
does not create recipes.

### `search_food(query, account=None, kind="food", page=0, limit=10)`

Searches food or drink entries. `kind` is `food` or `drink`.

### `list_custom_recipes(account, query="", page=0, limit=50)`

Lists private custom recipes/meals from
`/user/settings/meal/list?format=json&page=...&limit=...&query=...`. Use this
after `create_custom_recipe` to verify the recipe exists and to retrieve its
`recipe_guid` for `record_recipe_serving`.

### `record_food(...)`

Preview:

```json
{
  "account": "personal",
  "date": "2026-05-18",
  "meal_type": "lunch",
  "query": "rýže vařená",
  "amount": 150,
  "unit": "g"
}
```

Write:

```json
{
  "account": "personal",
  "date": "2026-05-18",
  "meal_type": "lunch",
  "query": "rýže vařená",
  "amount": 150,
  "unit": "g",
  "commit": true
}
```

### `record_food_batch(account, date, items, commit=false)`

Normalized payload suitable for Mealie/assistant workflows:

```json
{
  "account": "personal",
  "date": "2026-05-18",
  "default_meal_type": "lunch",
  "items": [
    {"name": "kuřecí prsa", "amount": 180, "unit": "g", "source": "mealie"},
    {"name": "rýže vařená", "amount": 150, "unit": "g", "source": "mealie"}
  ]
}
```

Add `"commit": true` only after reviewing the preview.

### `prepare_recipe_import(account, title, ingredients, ...)`

Preview helper for Mealie-to-Kaloricke Tabulky recipe work. It resolves each
recipe ingredient to a Kaloricke Tabulky food payload and returns the matched
`food_guid`, unit selection, multiplier, and source metadata. Use this to review
matches before calling `create_custom_recipe`.

```json
{
  "account": "personal",
  "title": "Lasagne podle Mealie",
  "servings": 4,
  "source": "mealie",
  "source_id": "lasagne",
  "ingredients": [
    {"name": "mleté hovězí maso", "amount": 500, "unit": "g", "source": "mealie"},
    {"name": "krájená rajčata", "amount": 400, "unit": "g", "source": "mealie"}
  ]
}
```

### `create_custom_recipe(...)`

Previews or creates a real private Kaloricke Tabulky custom recipe. By default
the write endpoint is `/user/settings/meal/detail/edit/0?format=json`; pass
`recipe_guid` to update an existing custom recipe through
`/user/settings/meal/detail/edit/{recipe_guid}?format=json`. Writes happen only
with `"commit": true`. KT calculates nutrition from the resolved ingredient
items.

```json
{
  "account": "personal",
  "title": "Lasagne podle Mealie",
  "recipe_guid": "optional-existing-custom-recipe-guid",
  "servings": 4,
  "preparation_time_minutes": 60,
  "source": "mealie",
  "source_id": "lasagne",
  "ingredients": [
    {"name": "mleté hovězí maso", "amount": 500, "unit": "g", "source": "mealie"},
    {"name": "krájená rajčata", "amount": 400, "unit": "g", "source": "mealie"}
  ],
  "commit": true
}
```

The response includes `recipe_guid`. Use that GUID with `record_recipe_serving`
to log a portion of the created recipe. Preview responses return a compact
`payload_summary`; set `include_payload=true` only for diagnostics when the full
KT JSON body is needed.

### `upload_custom_recipe_image(...)`

Previews or uploads an image to an existing Kaloricke Tabulky custom recipe
through `/user/settings/meal/detail/{recipe_guid}/image/create?format=json`.
Writes happen only with `"commit": true`. Pass the image as raw base64 or a
`data:image/...;base64,...` URL; the server uses the authenticated KT session,
not browser cookies from DevTools captures.

```json
{
  "account": "personal",
  "recipe_guid": "created-kt-custom-recipe-guid",
  "image_base64": "data:image/jpeg;base64,...",
  "filename": "lasagne.jpg",
  "content_type": "image/jpeg",
  "commit": true
}
```

The observed KT multipart field name is handled as `file` by default. Override
`field_name` only if a future browser capture shows a different form field.

### `record_recipe_serving(...)`

Previews or records a serving of a Kaloricke Tabulky custom recipe after the
recipe exists in the account. Custom recipes are resolved through the same
Kaloricke Tabulky food/meal lookup and diary add flow as regular foods.

Preview:

```json
{
  "account": "personal",
  "date": "2026-06-01",
  "meal_type": "dinner",
  "query": "Lasagne podle Mealie",
  "amount": 1,
  "unit": "porce"
}
```

Write:

```json
{
  "account": "personal",
  "date": "2026-06-01",
  "meal_type": "dinner",
  "recipe_guid": "resolved-or-created-kt-guid",
  "amount": 1,
  "unit": "porce",
  "commit": true
}
```

### `get_diary_summary(account=None, date=None)`

Returns daily summary metrics.

### `get_diary_detail(account=None, date=None)`

Returns daily diary detail payload for diagnostics.

### `record_weight(account, weight_kg, date=None, commit=false)`

Previews or writes body weight. Requires explicit `account` and `commit=true` to write.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
python -m compileall src tests
```

Run locally:

```bash
python -m kaloricke_tabulky_mcp.main --transport streamable-http
```

## Notes

This project is not affiliated with Kaloricke Tabulky. It uses unofficial web endpoints that may change without notice.
