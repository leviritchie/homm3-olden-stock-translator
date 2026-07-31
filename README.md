# HoMM3 → stock Olden Era scenario translator

Downloadable Windows utility that converts Heroes of Might and Magic III `.h3m`
scenarios into Olden Era `.map` files. Buildings are substituted with equivalents (with sulfur notably becoming alchemical dust).
Many buildings have different visit angles/sizes, so scenery is culled to create space when there isn't enough for a hero to navigate.
Expect to run into some issues, but this gives you a decent start without having to recreate it by hand in the Olden Era editor.

## Installation

1. Download the latest **Source zip** from [Releases](../../releases)
   (`homm3-olden-stock-translator-*.zip`), or clone this repo.
2. Unzip it somewhere.
3. Double-click **`Convert-Map.bat`** (or run `Convert-Map.ps1`) to open the converter window.
4. In the GUI, choose:
   - your `.h3m`
   - Your `HeroesOldenEra_Data/StreamingAssets/Core.zip`
   - optional: your Olden `maps\` folder to auto-install the result
5. Click **Convert**, then start Olden Era and open the scenario as a custom game.

`Thirst_for_Power.map` is auto-detected beside `Core.zip` under `StreamingAssets/maps/`
(every stock Olden install has it). Override only if needed via `-TemplateMap` /
`--template-map` / `STOCK_TEMPLATE_MAP`.

First run downloads official CPython from python.org into `.runtime\` (SHA256
verified). There is no custom translator `.exe`.

### CLI / scripting example

```powershell
.\Convert-Map.ps1 -Cli `
  -H3m "D:\HoMM3\Maps\Twins.h3m" `
  -MapSid "vanilla_stock_twins" `
  -OutDir ".\artifacts\twins" `
  -StockCore "D:\Steam\steamapps\common\Heroes of Might and Magic Olden Era\HeroesOldenEra_Data\StreamingAssets\Core.zip" `
  -InstallMapsDir "D:\Steam\steamapps\common\Heroes of Might and Magic Olden Era\HeroesOldenEra_Data\StreamingAssets\maps"
```

## Substitutions

- Oceans are dried up, instead becoming pathable sand terrain at a reduced elevation.
- Underground layers are simulated using extra large maps with sections separated by portals
- Unsupported H3 objects (or victory types with no Olden parallel) warn, omit or fall back
  that piece, and still write the map. Warnings show in the CLI/GUI log and manifest.
- Quest titles/descriptions are LocKit SIDs; convert merges their English text into
  `Core.zip` `Lang/*/texts/customMaps.json` (a backup is created once). Restart Olden
  after convert if it was already running so Loc packs reload.
- Maps with events will merge a text-only dialog overlays into `Core.zip`
  (a backup is created once). This means some events may break when the game is patched, until you run the utility again.

## Players, towns, and access (v0.1.5+)

Olden expects compact player seats (`1..N` with the human as owner `1`). The
translator now derives final owners **after** town bindings:

- Mixed-faction AI town sets can split onto extra Olden seats (or demote minority
  towns to neutral when seats `1..8` are exhausted).
- Orphan AI / playable sides with heroes but no town can bind a nearby neutral town.
- Owners are then renumbered to the compact native scheme; victory and timed-event
  audiences follow those final seats (including expanded audiences when one H3
  color became multiple Olden owners).
- Stock-safe clearance keeps subterranean portal gates and town south-approach
  cells walkable (no Golden Era-only objects).
- Optional scenery diversify post-pass: `--enable-scenery-canon-postpass`.

## Neutral army strength

Heroes 3 and Olden Era use different systems to represent neutral stack strength. The utility calibrates this reasonably closely.
Your mileage may vary depending on the scenario.

Budgets use `requestedValue = round(H3_count_or_nominal × squadValue, 50)` with a
baked snapshot of Golden Era `h3_` unit economy numbers (balance constants, not
game assets). SpawnsCreator fills fights with Olden Era units instead of H3 units.

## Regression sample (contributors)

A RoE / AB / SoD fingerprint suite lives under `scenarios/vanilla_stock/`
(HotA rows soft-optional). With local HoMM3 maps + stock `Core.zip` paths filled
in the manifest:

```powershell
python tools/build_vanilla_stock_regression.py
python tools/validate_vanilla_stock_regression_contract.py
```

See `scenarios/vanilla_stock/README_regression.md`. This checks generated-artifact
metrics only; it does not prove in-game load or gameplay.

## License

MIT — see [LICENSE](LICENSE). Game assets remain their owners’ property; do not
redistribute them with this project.
