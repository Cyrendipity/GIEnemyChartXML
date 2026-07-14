Text and Image Data of GIEnemyChartv2.0.
It can extract data directly and easily from FXG or MXML files instead scanning PNG of that.

## Generate monster JSON fragments

`generate_monster_json.py` pairs the FXG and MXML files for every monster and generates the fields `Resistace`, `Skill`, `Notes`, `Shield`, and `Chargebar`.

```bash
python3 generate_monster_json.py
```

The default output directory is `generated_json`. Its category directories mirror the source categories, and `_generation_report.json` records source paths, extracted item counts, and anything that needs manual review.

Useful options:

```bash
python3 generate_monster_json.py --include '蕴光月守宫' --overwrite
python3 generate_monster_json.py --limit 20 --output sample_json
python3 generate_monster_json.py --dry-run
```

FXG is used for resistance values, rich text, and source icon paths. MXML supplies skill headings that are rasterized or absent in some FXG files. Shield and charge-bar objects contain only fields that can be determined from the mechanism text; unknown runtime data is intentionally omitted for manual completion.
