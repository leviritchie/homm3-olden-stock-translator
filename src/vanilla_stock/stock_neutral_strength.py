"""Calibrate stock SpawnsCreator budgets to H3 map neutral strength (OSS).

Formula (same shape as the private campaign translator):

    requestedValue = round_half_up(count_or_nominal * squadValue, 50)

``squadValue`` is the **stock Core** native unit tier median (computed at runtime
from the user's installed ``Core.zip``). Creature identity maps to an H3 tier via
a public CRTRAITS-index→tier table — no Golden Era ``h3_`` economy rows are shipped.

Regenerate the tier table from a private GE Core (optional maintainer tool)::

    python -m vanilla_stock.stock_neutral_strength --write-creature-tiers
"""

from __future__ import annotations

import json
import math
import os
import statistics
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

STRENGTH_MODEL_PATH = Path(__file__).with_name("h3_neutral_strength_model.json")

_MODEL_CACHE: dict[str, Any] | None = None
_TIER_MEDIAN_CACHE: dict[tuple[str, float], dict[int, float]] = {}


class StockNeutralStrengthError(ValueError):
    """Fail-closed H3→stock army budget error."""


def load_strength_model(path: Path | None = None) -> dict[str, Any]:
    global _MODEL_CACHE
    model_path = path or STRENGTH_MODEL_PATH
    if path is None and _MODEL_CACHE is not None:
        return _MODEL_CACHE
    if not model_path.is_file():
        raise StockNeutralStrengthError(f"missing H3 neutral strength model: {model_path}")
    doc = json.loads(model_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise StockNeutralStrengthError(f"strength model must be an object: {model_path}")
    if str(doc.get("schema") or "") != "vanilla_stock.h3_neutral_strength_model.v1":
        raise StockNeutralStrengthError(f"unsupported strength model schema in {model_path}")
    if path is None:
        _MODEL_CACHE = doc
    return doc


def rounded_requested_value(value: float, *, rounding: float) -> float:
    if rounding <= 0:
        raise StockNeutralStrengthError(f"requestedValueRounding must be > 0, got {rounding}")
    return float(math.floor((float(value) / rounding) + 0.5) * rounding)


def _nominal_counts(model: dict[str, Any]) -> dict[int, int]:
    raw = model.get("nominalH3RandomStackCountsByLevel")
    if not isinstance(raw, dict) or not raw:
        raise StockNeutralStrengthError("strength model missing nominalH3RandomStackCountsByLevel")
    out: dict[int, int] = {}
    for key, value in raw.items():
        level = int(key)
        count = int(value)
        if level < 1 or count < 1:
            raise StockNeutralStrengthError(f"invalid nominal count entry {key!r}: {value!r}")
        out[level] = count
    return out


def _creature_tiers(model: dict[str, Any]) -> dict[int, int]:
    raw = model.get("creatureTypeTiers")
    if not isinstance(raw, dict) or not raw:
        raise StockNeutralStrengthError("strength model missing creatureTypeTiers")
    out: dict[int, int] = {}
    for key, value in raw.items():
        ctype = int(key)
        tier = int(value)
        if tier < 1 or tier > 7:
            raise StockNeutralStrengthError(f"invalid creature tier for type {ctype}: {tier}")
        out[ctype] = tier
    return out


def stock_core_path_from_env() -> Path | None:
    raw = os.environ.get("STOCK_CORE") or os.environ.get("OLDEN_STOCK_CORE")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def stock_tier_median_squad_values(core_zip: Path) -> dict[int, float]:
    key = (str(core_zip.resolve()), core_zip.stat().st_mtime)
    cached = _TIER_MEDIAN_CACHE.get(key)
    if cached is not None:
        return dict(cached)
    by_tier: dict[int, list[float]] = defaultdict(list)
    with zipfile.ZipFile(core_zip) as core:
        for name in core.namelist():
            if not (name.startswith("DB/units/units_logics/") and name.endswith("_l.json")):
                continue
            doc = json.loads(core.read(name).decode("utf-8-sig"))
            for row in doc.get("array") or []:
                if not isinstance(row, dict):
                    continue
                unit_id = str(row.get("id") or "")
                if not unit_id or unit_id.startswith("h3_"):
                    continue
                tier = row.get("tier")
                squad_value = row.get("squadValue")
                if isinstance(tier, int) and 1 <= tier <= 7 and isinstance(squad_value, (int, float)):
                    by_tier[tier].append(float(squad_value))
    missing = [tier for tier in range(1, 8) if tier not in by_tier]
    if missing:
        raise StockNeutralStrengthError(
            f"stock Core.zip missing native unit squadValue medians for tier(s) {missing}: {core_zip}"
        )
    medians = {tier: float(statistics.median(values)) for tier, values in sorted(by_tier.items())}
    _TIER_MEDIAN_CACHE[key] = medians
    return dict(medians)


def _resolve_tier_medians(
    *,
    model: dict[str, Any],
    stock_core: Path | None,
) -> dict[int, float]:
    core = stock_core or stock_core_path_from_env()
    if core is not None:
        return stock_tier_median_squad_values(core)
    baked = model.get("tierMedianSquadValuesFromStockNative")
    if not isinstance(baked, dict) or not baked:
        raise StockNeutralStrengthError(
            "no STOCK_CORE / stock_core argument and strength model has no "
            "tierMedianSquadValuesFromStockNative fallback"
        )
    out: dict[int, float] = {}
    for key, value in baked.items():
        tier = int(key)
        if not isinstance(value, (int, float)) or float(value) <= 0:
            raise StockNeutralStrengthError(f"invalid baked stock median for tier {key}: {value!r}")
        out[tier] = float(value)
    return out


def h3_random_monster_level(entity: dict[str, Any]) -> int | None:
    animation = str(entity.get("templateAnimation") or "")
    prefix = "AVWmon"
    suffix = ".def"
    if not animation.startswith(prefix) or not animation.endswith(suffix):
        return None
    raw = animation[len(prefix) : -len(suffix)]
    if not raw.isdigit():
        return None
    return int(raw)


def creature_type_for_monster_entity(entity: dict[str, Any]) -> int | None:
    object_id = entity.get("templateObjectId")
    subtype = entity.get("templateSubtype")
    if object_id == 54 and isinstance(subtype, int) and subtype >= 0:
        return subtype
    return None


def stock_random_squad_requested_value(
    entity: dict[str, Any],
    *,
    model: dict[str, Any] | None = None,
    stock_core: Path | None = None,
) -> float:
    strength = model or load_strength_model()
    rounding = float(strength["requestedValueRounding"])
    nominal = _nominal_counts(strength)
    tiers = _creature_tiers(strength)
    medians = _resolve_tier_medians(model=strength, stock_core=stock_core)
    count = int(entity.get("count") or 0)
    source_key = entity.get("sourceKey") or entity.get("key")

    level = h3_random_monster_level(entity)
    if level is not None:
        if level not in nominal or level not in medians:
            raise StockNeutralStrengthError(f"unsupported AVWmon level {level} for {source_key}")
        effective_count = count if count > 0 else nominal[level]
        return rounded_requested_value(medians[level] * effective_count, rounding=rounding)

    creature_type = creature_type_for_monster_entity(entity)
    if creature_type is not None:
        tier = tiers.get(creature_type)
        if tier is None:
            raise StockNeutralStrengthError(
                f"no creatureTypeTiers entry for creatureType={creature_type} at {source_key}"
            )
        if tier not in medians:
            raise StockNeutralStrengthError(f"missing stock median for tier {tier} at {source_key}")
        effective_count = count if count > 0 else nominal[tier]
        return rounded_requested_value(medians[tier] * effective_count, rounding=rounding)

    raise StockNeutralStrengthError(
        f"cannot calibrate monster strength for {source_key}: "
        f"animation={entity.get('templateAnimation')!r} objectId={entity.get('templateObjectId')!r} "
        f"subtype={entity.get('templateSubtype')!r} count={count}"
    )


def stock_guard_requested_value(
    stacks: list[dict[str, int]],
    *,
    model: dict[str, Any] | None = None,
    stock_core: Path | None = None,
) -> float:
    strength = model or load_strength_model()
    rounding = float(strength["requestedValueRounding"])
    tiers = _creature_tiers(strength)
    medians = _resolve_tier_medians(model=strength, stock_core=stock_core)
    total = 0.0
    for stack in stacks:
        creature_type = int(stack["creatureType"])
        count = int(stack["count"])
        if count <= 0:
            continue
        tier = tiers.get(creature_type)
        if tier is None:
            raise StockNeutralStrengthError(
                f"no creatureTypeTiers entry for guard creatureType={creature_type} stacks={stacks!r}"
            )
        total += medians[tier] * count
    if total <= 0:
        raise StockNeutralStrengthError(f"guard requestedValue must be positive; stacks={stacks!r}")
    return rounded_requested_value(total, rounding=rounding)


def build_strength_model_from_stock_core(
    *,
    stock_core: Path,
    creature_tiers: dict[int, int],
    nominal: dict[int, int],
    rounding: float = 50.0,
) -> dict[str, Any]:
    medians = stock_tier_median_squad_values(stock_core)
    budgets = {str(tier): float(medians[tier]) * nominal[tier] for tier in sorted(nominal)}
    return {
        "schema": "vanilla_stock.h3_neutral_strength_model.v1",
        "model": "h3_count_x_stock_tier_median_squadValue_v1",
        "sourceStatus": "generated_from_stock_core_native_unit_medians",
        "generatedDate": str(date.today()),
        "sourceNote": (
            "requestedValue = round_half_up(count_or_nominal * stock_native_tier_median_squadValue, 50). "
            "creatureTypeTiers is public H3 CRTRAITS index→tier knowledge. "
            "At emit time, prefer live STOCK_CORE medians over the baked snapshot. "
            "Do not bake NeutralsDifficulty into requestedValue."
        ),
        "nominalH3RandomStackCountsByLevel": {str(k): v for k, v in sorted(nominal.items())},
        "requestedValueRounding": float(rounding),
        "tierMedianSquadValuesFromStockNative": {str(k): v for k, v in sorted(medians.items())},
        "randomTierNominalBudgets": budgets,
        "creatureTypeTiers": {str(k): v for k, v in sorted(creature_tiers.items())},
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stock-core",
        type=Path,
        default=Path(os.environ["STOCK_CORE"]) if os.environ.get("STOCK_CORE") else None,
    )
    parser.add_argument("--out", type=Path, default=STRENGTH_MODEL_PATH)
    parser.add_argument(
        "--creature-tiers-from",
        type=Path,
        help="Optional JSON with creatureTypeTiers (or full prior model) to preserve tier map",
    )
    args = parser.parse_args(argv)
    if args.stock_core is None or not args.stock_core.is_file():
        raise SystemExit("pass --stock-core or set STOCK_CORE to stock Olden Core.zip")

    if args.creature_tiers_from and args.creature_tiers_from.is_file():
        prior = json.loads(args.creature_tiers_from.read_text(encoding="utf-8"))
        tiers_raw = prior.get("creatureTypeTiers") or {
            k: v["tier"] for k, v in (prior.get("creatureTypeSquadValuesFromGe") or {}).items()
        }
        creature_tiers = {int(k): int(v if not isinstance(v, dict) else v["tier"]) for k, v in tiers_raw.items()}
    else:
        creature_tiers = {int(k): int(v) for k, v in load_strength_model()["creatureTypeTiers"].items()}

    campaign = Path(__file__).resolve().parents[1] / "h3_random_monster_strength_model.json"
    nominal_doc = json.loads(campaign.read_text(encoding="utf-8"))
    nominal = {int(k): int(v) for k, v in nominal_doc["nominalH3RandomStackCountsByLevel"].items()}
    payload = build_strength_model_from_stock_core(
        stock_core=args.stock_core,
        creature_tiers=creature_tiers,
        nominal=nominal,
        rounding=float(nominal_doc.get("requestedValueRounding") or 50.0),
    )
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    global _MODEL_CACHE
    _MODEL_CACHE = None
    print(json.dumps({"out": str(args.out), "budgets": payload["randomTierNominalBudgets"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
