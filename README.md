Text and Image Data of GIEnemyChartv2.0.
It can extract data directly and easily from FXG or MXML files instead scanning PNG of that.

## Generate monster JSON

`generate_monster_json.py` pairs the FXG and MXML files for every monster and emits the full object shape used by `Magbeast_Gecko.json`. In addition to resistance, skills, notes, shields, and charge bars, it restores base HP/ATK from `MonsterCurveExcelConfigData.json` and maps the chart's energy notation to an `EnergyGroup` from `MonsterEnergyDropExcelData.json`.

```bash
python3 generate_monster_json.py
```

The default output directory is `generated_json`. JSON filenames follow `MonsterCatalogExcelData.json`: every unique `InternalName` becomes `<InternalName>.json`, the JSON outer key, and the object `Id`. One chart source may expand into several catalog files when multiple catalog entries share its `ImagePath`.

`MonsterCatalogExcelData.json` is the authoritative monster catalog, while `LeylineChallengeLevelExcelData.json` maps Leyline chart titles to `MonsterId` and supplies coefficients, default difficulty levels, campaign names, and recommended or discouraged tactics. Leyline charts use their dedicated object shape (`NormName`, `HardName`, `DireName`, `Coefficients`, `DefaultLevels`, `SkillDiffer`, and `MechanismNotes`). Catalog entries without an independent FXG/MXML chart still receive a complete-shape placeholder JSON populated with catalog names, base HP/ATK, curves, and multiplayer group; unavailable fields remain empty. `_catalog_coverage_report.json` lists placeholders, duplicate catalog IDs, and unmatched chart sources.

The curve and energy JSON files are loaded from the repository root by default. Alternate paths can be supplied with `--curve-data`, `--energy-data`, `--catalog-data`, and `--leyline-data`.

Skill icons are matched automatically against `icons/**/*.png`. Both the FXG `image_N.png` and the runtime icon are decoded as RGBA, then SHA-256 hashed using the image dimensions followed by the pixel bytes. An exact match is emitted as `static/images/MonsterSkillIcon/...` or `static/images/UIIcon/...`. Use `--icons-root` to select another icon tree. Verified JSON files remain fallback references for icons that have no hash match; `Magbeast_Gecko.json` and `Watcher_Primo_Leyline.json` are loaded by default, and `--reference-json` can add more. The Watcher chart also has a narrowly scoped mechanism rule that derives its four elemental monitoring meters and burst countdown from the source text.

Useful options:

```bash
python3 generate_monster_json.py --include '蕴光月守宫' --overwrite
python3 generate_monster_json.py --reference-json AnotherMonster.json
python3 generate_monster_json.py --limit 20 --output sample_json
python3 generate_monster_json.py --dry-run
python3 generate_monster_json.py --overwrite --clean-output
```

Every normal run also creates or updates the flat `collected_json` directory. Use `--collect-output` to choose another destination or `--no-collect` to disable it. Files beginning with `_`, including both reports, are excluded. With `--overwrite`, stale JSON files in the flat destination are removed before copying. Collection can also run independently against the existing output directory:

```bash
python3 generate_monster_json.py --overwrite
python3 generate_monster_json.py --collect-only --output generated_json --overwrite
```

FXG is used for resistance values and rich text. MXML supplies skill headings that are rasterized or absent in some FXG files. Shield and charge-bar objects contain only fields that can be determined from the mechanism text; unknown runtime data is intentionally omitted for manual completion.
