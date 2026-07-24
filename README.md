# HoMM3 → stock Olden Era scenario translator

MIT-licensed Python tools that convert standalone Heroes of Might and Magic III
`.h3m` scenarios into **stock-legal** Heroes of Might and Magic: Olden Era `.map`
files.

This lane does **not** depend on Golden Era / custom Core overlays or the
OfflineUnlockMod plugin. Output SIDs and tiles must exist in stock Olden
`Core.zip`.

## What you must own (not redistributed here)

You need your own copies of:

1. A HoMM3 `.h3m` map (Complete / SoD / HotA, etc.)
2. Stock Olden Era install files:
   - `HeroesOldenEra_Data/StreamingAssets/Core.zip`
   - a stock template scenario such as `maps/Thirst_for_Power.map`

This repository ships **translator source only**. It does not include game
binaries, `Core.zip`, template maps, or commercial `.h3m` files.

## Quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e .

export STOCK_CORE="/path/to/HeroesOldenEra_Data/StreamingAssets/Core.zip"
export STOCK_TEMPLATE_MAP="/path/to/maps/Thirst_for_Power.map"
# optional install target (stock maps folder):
export STOCK_MAPS_DIR="/path/to/maps"

python tools/build_vanilla_stock_map.py \
  --h3m /path/to/Twins.h3m \
  --out-dir ./artifacts/twins \
  --map-sid vanilla_stock_twins \
  --install-maps-dir "$STOCK_MAPS_DIR"
```

Or with the console script after `pip install -e .`:

```bash
homm3-olden-stock-map --h3m Twins.h3m --out-dir ./artifacts/twins --map-sid vanilla_stock_twins
```

## Neutral army strength

Random and typed neutrals are budgeted for Olden `propRandomSquads.requestedValue` as:

`round_half_up(H3_count_or_nominal × squadValue, 50)`

`squadValue` comes from a **baked snapshot of Golden Era `h3_` unit economy numbers**
(ported H3 strength in Olden value space). Those are balance constants, not game
assets. Stock SpawnsCreator still fills the budget with **stock** native units.

Regenerating the snapshot (maintainers only) needs a local GE `Core.zip` and the
private monorepo creature-type map via `GE_CORE` + `MONOREPO_SURFACE_EMIT`.

## Limits (fail-closed)

- Stock Core tiles are `{1..7}` only. Ocean uses a Sand basin stand-in; underground
  walkable cells and elevated rock use Dirt (no GE Burrow/Water/Void tiles).
- Unsupported H3 objects/victory types fail closed with diagnostics.
- Map-event dialogs may write a small text-only LocKit overlay into `Core.zip`
  when events are present (`tools/install_vanilla_stock_event_dialog_overlay.py`).
  That mutates your install; a backup is created once per Core path.
- Runtime load / combat feel in stock Olden remains **unvalidated** until you prove it.

## Layout

```
src/vanilla_stock/     # translator package
src/*.py               # H3 decode + Olden map I/O helpers
tools/                 # CLI entrypoints
examples/              # sample batch manifest (paths are placeholders)
```

## License

MIT — see [LICENSE](LICENSE). Game assets remain the property of their respective
owners; do not redistribute them with this project.
