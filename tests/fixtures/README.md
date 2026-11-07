`diary_synthetic.json` is a deliberately synthetic contract fixture. It was NOT
captured from KT and is not evidence of the live endpoint's field meanings.

Before phase 2 can be considered complete, add an anonymized live diary capture,
verify food versus diary entry IDs, amount/unit fields, nutrient units and whether
nutrients are per unit or entry totals. The compact adapter currently reads only
explicit `energy_kcal`, `protein_g`, `carbohydrate_g` and `fat_g` entry totals;
missing totals remain null. Do not invent scaling rules or a deletion endpoint.
