`diary_live_anonymized.json` is an excerpt of a live KT
`user/diary/{date}/get` response (November 2026). Titles and GUIDs are replaced;
the structure, unit texts and nutrient values are kept.

Verified against the live service:

- meals are `data.times[]` with the meal ID (`1`–`6`) in `id`; rows are in
  `foodstuff[]` and `activities`, `notes` and top-level totals sit beside them;
- `id` is the diary entry, `guidType` the food GUID from search or the custom
  recipe GUID (also in `guidUserMeal` for recipe servings);
- the portion is only the display text `unit`, e.g. `1,5 x kus (42 g)`;
- `energy` (with `energyUnit`) and `protein`, `carbohydrate`, `fat`, `fiber`,
  `sugar`, `salt` are totals for the whole entry; `null` means unknown;
- an empty day returns all six meals with empty `foodstuff` lists.

The deletion request is still unknown; do not invent an endpoint.
