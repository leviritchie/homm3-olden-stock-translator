# HoMM3 → stock Olden Era scenario translator

Downloadable Windows utility that converts Heroes of Might and Magic III `.h3m`
scenarios into Olden Era `.map` files. Buildings are substituted with equivalents (with sulfur notably becoming alchemical dust).
Many buildings have different visit angles/sizes, so scenery is culled to create space when there isn't enough for a hero to navigate.
Expect to run into some issues, but this gives you a decent start without having to recreate it by hand in the Olden Era editor.

## Installation

1. Download the latest **Source zip** from [Releases](../../releases)
   (`homm3-olden-stock-translator-*.zip`), or clone this repo.
2. Unzip it somewhere.
3. Double-click **`Convert-Map.bat`** (or run `Convert-Map.ps1`).
4. When prompted, provide:
   - your `.h3m`
   - Your `HeroesOldenEra_Data/StreamingAssets/Core.zip`
   - optional: your Olden `maps\` folder to auto-install the result
5. Start Olden Era and open the scenario as a custom game.

`Thirst_for_Power.map` is auto-detected beside `Core.zip` under `StreamingAssets/maps/`
(every stock Olden install has it). Override only if needed via `-TemplateMap` /
`--template-map` / `STOCK_TEMPLATE_MAP`.

### Example

```powershell
.\Convert-Map.ps1 `
  -H3m "D:\HoMM3\Maps\Twins.h3m" `
  -MapSid "vanilla_stock_twins" `
  -OutDir ".\artifacts\twins" `
  -StockCore "D:\Steam\steamapps\common\Heroes of Might and Magic Olden Era\HeroesOldenEra_Data\StreamingAssets\Core.zip" `
  -InstallMapsDir "D:\Steam\steamapps\common\Heroes of Might and Magic Olden Era\HeroesOldenEra_Data\StreamingAssets\maps"
```

## Substitions

- Oceans are dried up, instead becoming pathable sand terrain at a reduced elevation.
- Underground layers are simulated using extra large maps with sections separated by portals
- Unsupported H3 objects (or victory types with no Olden parallel) warn, omit or fall back
  that piece, and still write the map. Warnings show in the CLI summary and manifest.
- Maps with events will merge a text-only dialog overlays into `Core.zip`
  (a backup is created once). This means some events may break when the game is patched, until you run the utility again.

## Neutral army strength

Heroes 3 and Olden Era use different systems to represent neutral stack strength. The utility calibrates this reasonably closely.
Your mileage may vary depending on the scenario.

Budgets use `requestedValue = round(H3_count_or_nominal × squadValue, 50)` with a
baked snapshot of Golden Era `h3_` unit economy numbers (balance constants, not
game assets). SpawnsCreator fills fights with Olden Era units instead of H3 units.

## License

MIT — see [LICENSE](LICENSE). Game assets remain their owners’ property; do not
redistribute them with this project.
