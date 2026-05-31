# Kaloricke Tabulky MCP

MCP server for searching and writing Kaloricke Tabulky food diary entries through the unofficial web endpoints used by the Kaloricke Tabulky web app.

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
ghcr.io/nikopol666/kaloricke-tabulky-mcp:latest
```

`latest` is published only from semver release tags.

## Environment

```env
KT_ACCOUNTS=personal,partner
KT_ACCOUNT_PERSONAL_EMAIL=user@example.com
KT_ACCOUNT_PERSONAL_PASSWORD=replace-me
KT_ACCOUNT_PARTNER_EMAIL=partner@example.com
KT_ACCOUNT_PARTNER_PASSWORD=replace-me
KT_DEFAULT_ACCOUNT=personal
MCP_BEARER_TOKEN=replace-me-long-random-token
MCP_HTTP_PORT=8080
```

Aliases are normalized to lowercase underscores. Collisions such as `my-account` and `my_account` are rejected.
When `MCP_BEARER_TOKEN` is set, MCP clients must send `Authorization: Bearer <token>` to `/mcp`. `/health` stays unauthenticated for container healthchecks.

## Tools

### `list_accounts`

Lists safe account aliases and masked email addresses.

### `check_account_auth(account)`

Runs an explicit login check for one configured account.

### `search_food(query, account=None, kind="food", page=0, limit=10)`

Searches food or drink entries. `kind` is `food` or `drink`.

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

Preview-only helper for Mealie-to-Kaloricke Tabulky recipe work. It resolves each
recipe ingredient to a Kaloricke Tabulky food payload and returns the matched
`food_guid`, unit selection, multiplier, and source metadata.

This tool does not create a Kaloricke Tabulky custom recipe yet. The upstream
custom recipe create/save endpoint is private and must be verified from the web
app network requests before writes are enabled.

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
