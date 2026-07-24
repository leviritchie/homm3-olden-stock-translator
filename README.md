# HoMM3 → stock Olden Era scenario translator

Downloadable Windows utility that converts Heroes of Might and Magic III `.h3m`
scenarios into **stock-legal** Olden Era `.map` files.

No Golden Era install, no custom plugin, and **no translator `.exe`** from this
project. The release is readable source plus a launcher.

## For players (no Python install required)

1. Download the latest **Source zip** from [Releases](../../releases)
   (`homm3-olden-stock-translator-*.zip`), or clone this repo.
2. Unzip it somewhere permanent.
3. Double-click **`Convert-Map.bat`** (or run `Convert-Map.ps1`).
4. First run downloads **official CPython** from [python.org](https://www.python.org/)
   into a local `.runtime\` folder and verifies the **SHA256** checksum, then
   installs this translator with pip.
5. When prompted, provide:
   - your `.h3m`
   - stock Olden `HeroesOldenEra_Data/StreamingAssets/Core.zip`
   - a stock template map such as `maps/Thirst_for_Power.map`
   - optional: your Olden `maps\` folder to auto-install the result
6. Start **stock** Olden Era and open the scenario (the SID you chose).

### Non-interactive example

```powershell
.\Convert-Map.ps1 `
  -H3m "D:\HoMM3\Maps\Twins.h3m" `
  -MapSid "vanilla_stock_twins" `
  -OutDir ".\artifacts\twins" `
  -StockCore "D:\Steam\steamapps\common\Heroes of Might and Magic Olden Era\HeroesOldenEra_Data\StreamingAssets\Core.zip" `
  -TemplateMap "D:\Steam\steamapps\common\Heroes of Might and Magic Olden Era\HeroesOldenEra_Data\StreamingAssets\maps\Thirst_for_Power.map" `
  -InstallMapsDir "D:\Steam\steamapps\common\Heroes of Might and Magic Olden Era\HeroesOldenEra_Data\StreamingAssets\maps"
```

### Trust model

| Piece | Who provides it |
|--------|------------------|
| Translator logic | This repo (Python source you can read) |
| Python runtime | python.org embeddable build (checksum pinned in `Convert-Map.ps1`) |
| Game data | Your own Olden + HoMM3 installs (never redistributed here) |

We intentionally **do not** ship a frozen/custom `.exe` of the converter.

## What you must own

1. A HoMM3 `.h3m` (Complete / SoD / HotA, etc.)
2. Stock Olden Era:
   - `StreamingAssets/Core.zip`
   - a stock template `.map` (e.g. `Thirst_for_Power.map`)

## Limits (fail-closed)

- Stock tiles are `{1..7}` only. Ocean uses a Sand basin stand-in; underground /
  elevated rock use Dirt (no Golden Era Burrow/Water/Void tiles).
- Unsupported H3 objects or victory types stop with a clear error.
- Maps with events may merge a small text-only dialog overlay into `Core.zip`
  (a backup is created once). Review that before running on a precious install.
- Runtime playability in stock Olden is **not** claimed until you verify it.

## Neutral army strength

Budgets use `requestedValue = round(H3_count_or_nominal × squadValue, 50)` with a
baked snapshot of Golden Era `h3_` unit economy numbers (balance constants, not
game assets). SpawnsCreator still fills fights with **stock** units.

## Developers (already have Python)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
$env:STOCK_CORE = "...\Core.zip"
$env:STOCK_TEMPLATE_MAP = "...\Thirst_for_Power.map"
python tools/build_vanilla_stock_map.py --h3m Twins.h3m --out-dir .\artifacts\twins --map-sid vanilla_stock_twins
```

### Publishing a release zip

```powershell
python tools/build_release_zip.py --version 0.1.0
# upload release_dist/homm3-olden-stock-translator-0.1.0.zip to GitHub Releases
```

## License

MIT — see [LICENSE](LICENSE). Game assets remain their owners’ property; do not
redistribute them with this project.
