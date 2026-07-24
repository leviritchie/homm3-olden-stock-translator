"""Emit a stock-legal Olden .map from a standalone HoMM3 .h3m.

Pipeline mirrors raw_translation (without GE overlays / homm3_* SIDs):

1. Build alignment IR from the standalone .h3m (propEntities + layeredMapData)
2. Project terrain into stock Core tile/water allowlists (Sand ocean basin,
   Dirt subterranean/rock, biome waterMap rivers, Chebyshev-2 tunnel clearance)
3. Emit stock-SID objects from IR entities (substitution table + stock footprints)
4. Apply stock-safe gate-face rotation
5. Write placement ground truth + fail-closed stock validator
"""

from __future__ import annotations

import hashlib
import os
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import h3m_format as h3m
import h3m_object_registry as h3obj
import h3m_scenario_translation as scenario
import native_only_stock as native
import port_homecoming_poc as poc
from h3m_object_walk import read_h3m_bytes

from . import (
    PIPELINE,
    SCHEMA_MAP,
    STATUS,
    STOCK_SUBTERRANEAN_GATE_SID,
)
from .alignment_ir import VanillaStockAlignmentError, build_alignment_ir
from .gate_face import VanillaStockGateFaceError, apply_stock_gate_face_rotations
from .object_map import (
    DEFAULT_STOCK_HERO_BY_FACTION,
    H3_TERRAIN_BIOME,
    VanillaStockObjectMapError,
    resolve_object_sid,
)
from .placement_ground_truth import build_placement_ground_truth
from .scenery_footprint import (
    POLICY as SCENERY_FOOTPRINT_POLICY,
    SCHEMA as SCENERY_FOOTPRINT_SCHEMA,
    VanillaStockSceneryFootprintError,
    load_stock_object_configs,
    occupied_nodes_for_instance,
    plan_stock_scenery,
)
from .substitution import StockSubstitutionTableError, load_substitution_table
from .terrain import (
    WATER_PROJECTION_POLICY,
    VanillaStockTerrainError,
    apply_stock_ocean_basin_geometry,
    assert_stock_tile_ids,
    assert_stock_water_ids,
    build_empty_atlas_arrays,
    load_stock_tile_ids,
    load_stock_water_ids,
    paint_envelope_padding_elevated_dirt,
    project_layer_into_atlas,
    single_view_for_atlas,
    terrain_policy_manifest,
)
import town_gate_align
import homm3_olden_rarity_bin as rarity_bin
from .victory_events import (
    STOCK_MAP_EVENT_DECO_SID,
    STOCK_MAP_EVENT_GUARD_SID,
    STOCK_MAP_EVENT_MARKER_SID,
    VanillaStockVictoryError,
    apply_global_timed_events,
    apply_map_events,
    apply_victory_contract,
    classify_map_event_guards,
    source_mine_records,
    stock_monster_reaction_type,
    stock_random_squad_property_row,
    stock_random_squad_requested_value,
)


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    return Path(raw)


DEFAULT_STOCK_CORE = _env_path("STOCK_CORE")
DEFAULT_STOCK_TEMPLATE_MAP = _env_path("STOCK_TEMPLATE_MAP")
DEFAULT_STOCK_MAPS_DIR = _env_path("STOCK_MAPS_DIR")
DEFAULT_SUBSTITUTION_TABLE = Path(__file__).with_name("substitution_table.json")
STOCK_TEMPLATE_MAP_BASENAME = "Thirst_for_Power.map"

CITY_BUILDINGS_BAN_SID = "default_buildings_ban"
CITY_BUILDINGS_CONSTRUCTION_SID = "rich_buildings_construction"
CITY_BUILDINGS_SETTINGS_SID = "default_buildings_settings"
FORBIDDEN_SID_SUBSTRINGS = ("homm3", "h3_", "golden_era")


class VanillaStockEmitError(ValueError):
    """Raised when a stock map cannot be emitted without hiding a mismatch."""


def stock_template_map_beside_core(stock_core: Path) -> Path:
    """Every stock install ships Thirst beside Core.zip under StreamingAssets/maps/."""

    return stock_core.resolve().parent / "maps" / STOCK_TEMPLATE_MAP_BASENAME


def resolve_stock_template_map(stock_core: Path, template_map: Path | None) -> Path:
    if template_map is not None:
        return Path(template_map)
    if DEFAULT_STOCK_TEMPLATE_MAP is not None:
        return DEFAULT_STOCK_TEMPLATE_MAP
    return stock_template_map_beside_core(stock_core)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def slugify_map_sid(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(title).lower()).strip("_")
    return f"vanilla_stock_{slug or 'map'}"


def _core_array_ids(core: Path, prefixes: tuple[str, ...]) -> set[str]:
    ids: set[str] = set()
    try:
        with zipfile.ZipFile(core) as archive:
            for member in archive.namelist():
                normalized = member.replace("\\", "/").lower()
                if not normalized.endswith(".json") or not normalized.startswith(prefixes):
                    continue
                try:
                    payload = json.loads(archive.read(member).decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                rows = payload.get("array") if isinstance(payload, dict) else None
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if isinstance(row, dict) and isinstance(row.get("id"), str):
                        ids.add(row["id"])
    except (OSError, zipfile.BadZipFile) as ex:
        raise VanillaStockEmitError(f"cannot read stock Core.zip: {core}: {ex}") from ex
    return ids


def load_stock_object_ids(core: Path) -> set[str]:
    ids = native.load_core_object_ids(core)
    if not ids:
        raise VanillaStockEmitError(f"stock Core has no ObjectConfig IDs: {core}")
    return ids


def load_stock_faction_ids(core: Path) -> set[str]:
    ids = native.load_core_faction_ids(core)
    if not ids:
        raise VanillaStockEmitError(f"stock Core has no faction IDs: {core}")
    return ids


def load_stock_hero_ids(core: Path) -> set[str]:
    ids = _core_array_ids(core, ("db/heroes/", "db/hero/", "db/characters/"))
    if not ids:
        raise VanillaStockEmitError(f"stock Core has no hero IDs: {core}")
    return ids


def empty_object_properties(_template: dict[str, Any]) -> dict[str, Any]:
    return {
        "propCities": [],
        "propPortals": [],
        "propSpawns": [],
        "propHeroes": [],
        "propRandomSquads": [],
        "propOwners": [],
        "propRandomItems": [],
        "propEntities": [],
        "propActionsBefore": [],
        "propActionsAfter": [],
        "propActivations": [],
        "propMarkers": [],
        "propRewardParams": [],
        "propQuestMarkers": [],
        "propQuestNames": [],
        "propDialogWindows": [],
    }


def append_object_instance(
    objects: list[dict[str, Any]], sid: str, object_id: int, node: int, rotation: int = 0
) -> None:
    if any(token in sid.lower() for token in FORBIDDEN_SID_SUBSTRINGS):
        raise VanillaStockEmitError(f"refusing GE/h3 SID leak: {sid}")
    for group in objects:
        if group.get("sid") == sid:
            group["ids"].append(object_id)
            group["nodes"].append(node)
            group["rotations"].append(rotation)
            group["levels"].append(0.0)
            return
    objects.append({"sid": sid, "ids": [object_id], "nodes": [node], "rotations": [rotation], "levels": [0.0]})


def remove_object_instance(objects: list[dict[str, Any]], object_id: int) -> str | None:
    """Remove one emitted instance by id. Returns the SID that held it, if any."""

    for group in objects:
        ids = group.get("ids")
        if not isinstance(ids, list) or object_id not in ids:
            continue
        index = ids.index(object_id)
        for key in ("ids", "nodes", "rotations", "levels"):
            values = group.get(key)
            if isinstance(values, list) and index < len(values):
                values.pop(index)
        sid = str(group.get("sid") or "")
        if not ids:
            objects.remove(group)
        return sid or None
    return None


def terrain_biome_at(layer_tiles_by_key: dict[str, dict[str, Any]], layer: int, x: int, y: int) -> str:
    tile = layer_tiles_by_key.get(f"{layer}:{x}:{y}")
    if not isinstance(tile, dict):
        return "grass"
    try:
        terrain_id = int(tile.get("terrain", 0))
    except (TypeError, ValueError):
        terrain_id = 0
    return H3_TERRAIN_BIOME.get(terrain_id, "grass")


# HoMM3 unowned / neutral object owner byte (matches approach_cell H3_NEUTRAL_OWNER).
H3_NEUTRAL_OWNER = 255


def h3_owner_to_olden(owner: Any) -> int | None:
    """Map H3 owner byte to Olden 1-based player index; neutral 255 → None."""
    if owner is None or owner == H3_NEUTRAL_OWNER:
        return None
    if not isinstance(owner, int) or owner < 0 or owner > 7:
        raise VanillaStockEmitError(f"unsupported H3 owner index {owner!r}")
    return owner + 1


def _stock_random_item_rarity(entity: dict[str, Any]) -> int:
    """Map H3 random-artifact (or lossy specific artifact→random-item) to Olden rarity."""
    template_id = int(entity.get("templateObjectId") or 0)
    if template_id in rarity_bin.H3_RANDOM_ARTIFACT_OBJECT_ID_TO_HOMM_RARITY:
        return rarity_bin.random_artifact_rarity(entity)
    if template_id == h3obj.OBJECT_ARTIFACT:
        # Specific H3 artifacts remapped to stock random-item: bin as Olden rarity 1.
        return rarity_bin.bin_homm3_rarity_to_olden_erarity(0)
    raise VanillaStockEmitError(
        f"no Olden rarity mapping for random-item templateObjectId={template_id} "
        f"at {entity.get('sourceKey')}"
    )


def _entity_key(entity: dict[str, Any]) -> str:
    return str(entity.get("sourceKey") or f"object_{entity.get('sourceIndex')}")


def _entity_as_object_map_record(entity: dict[str, Any]) -> dict[str, Any]:
    """Adapt IR entity fields to the walk-record shape object_map expects."""
    record = dict(entity)
    record["index"] = int(entity["sourceIndex"])
    record["key"] = _entity_key(entity)
    record["x"] = int(entity["sourceX"])
    record["y"] = int(entity["sourceY"])
    record["layer"] = int(entity["sourceLayer"])
    record["z"] = int(entity["sourceLayer"])
    return record


def _ensure_stock_required_ids(stock_objects: set[str]) -> None:
    required = {
        STOCK_SUBTERRANEAN_GATE_SID,
        "random-squad",
        "random-res",
        "random-item",
        "random-city",
        "chest",
        "human_city",
        "nature_city",
        "dungeon_city",
        "demon_city",
        "undead_city",
        "mine_gold",
        "mine_mercury",
        "campaign_M2_empty_mine",
    }
    missing = sorted(required - stock_objects)
    if missing:
        raise VanillaStockEmitError(f"required stock ObjectConfig missing: {missing}")


def _occupied_nodes_for_event_relocation(
    objects: list[dict[str, Any]],
    *,
    stock_object_configs: dict[str, dict[str, Any]],
    atlas_width: int,
    atlas_height: int,
) -> set[int]:
    """Reserve object anchors + ObjectConfig footprints so Zone markers do not stack."""

    import sys
    from pathlib import Path

    approach = Path(__file__).resolve().parents[1] / "approach_cell"
    if str(approach) not in sys.path:
        sys.path.insert(0, str(approach))
    import surface_emit as single

    previous_w, previous_h = single.OLDEN_WIDTH, single.OLDEN_HEIGHT
    single.OLDEN_WIDTH = atlas_width
    single.OLDEN_HEIGHT = atlas_height
    try:
        occupied: set[int] = set()
        for group in objects:
            if not isinstance(group, dict):
                continue
            sid = str(group.get("sid") or "")
            config = stock_object_configs.get(sid)
            nodes = group.get("nodes") or []
            rotations = group.get("rotations") or []
            for index, node in enumerate(nodes):
                if not isinstance(node, int):
                    continue
                occupied.add(int(node))
                if not isinstance(config, dict):
                    continue
                rotation = 0
                if index < len(rotations) and rotations[index] is not None:
                    rotation = int(rotations[index] or 0)
                occupied |= set(
                    single.occupied_nodes_for_object_instance(sid, config, int(node), rotation)
                )
        return occupied
    finally:
        single.OLDEN_WIDTH = previous_w
        single.OLDEN_HEIGHT = previous_h


def _envelope_nodes_for_atlas(atlas: Any) -> set[int]:
    nodes: set[int] = set()
    for layer in atlas.layers:
        for y in range(atlas.source_height):
            for x in range(atlas.source_width):
                nodes.add(atlas.target_node(layer, x, y))
    return nodes


def _table_first_decision(
    *,
    table: Any,
    record: dict[str, Any],
    stock_objects: set[str],
) -> dict[str, Any] | None:
    try:
        return table.resolve(record, stock_objects)
    except StockSubstitutionTableError as ex:
        raise VanillaStockEmitError(str(ex)) from ex


def _prune_scenery_footprints_after_gate_face(
    scenery_footprint_rows: list[dict[str, Any]],
    *,
    cleared_ids: set[int],
    stock_object_configs: dict[str, dict[str, Any]],
    atlas_width: int,
    atlas_height: int,
) -> dict[str, int]:
    """Drop approach-cleared placements and retarget expectedNodes to survivors."""
    if not cleared_ids:
        return {"prunedRowCount": 0, "prunedPlacementCount": 0, "droppedRowCount": 0}
    pruned_rows = 0
    pruned_placements = 0
    dropped_rows = 0
    surviving: list[dict[str, Any]] = []
    for row in scenery_footprint_rows:
        placements = row.get("placements") or []
        kept = [p for p in placements if int(p["id"]) not in cleared_ids]
        removed = len(placements) - len(kept)
        if removed:
            pruned_placements += removed
            pruned_rows += 1
            row = dict(row)
            row["placements"] = kept
            row["approachClearedPlacementCount"] = removed
            remaining_nodes: set[int] = set()
            for placement in kept:
                config = stock_object_configs.get(str(placement["sid"]))
                if config is None:
                    continue
                remaining_nodes |= occupied_nodes_for_instance(
                    config,
                    anchor_node=int(placement["node"]),
                    rotation=int(placement.get("rotation") or 0),
                    width=atlas_width,
                    height=atlas_height,
                )
            row["expectedNodes"] = sorted(remaining_nodes)
            row["expectedNodesNote"] = "pruned_after_gate_face_approach_clear"
        if not row.get("placements"):
            dropped_rows += 1
            continue
        surviving.append(row)
    scenery_footprint_rows[:] = surviving
    return {
        "prunedRowCount": pruned_rows,
        "prunedPlacementCount": pruned_placements,
        "droppedRowCount": dropped_rows,
    }


def _force_stock_travel_decision(record: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Override substitution-table cave→portal_1 so subterranean gates stay pairable."""
    oid = int(record.get("templateObjectId") or -1)
    if oid == h3obj.OBJECT_SUBTERRANEAN_GATE:
        return {
            "action": "emit",
            "sid": STOCK_SUBTERRANEAN_GATE_SID,
            "kind": "portal",
            "reason": "stock_subterranean_gate_portal_5_cross_layer",
            "travelLink": "subterranean_gate",
        }
    return decision


def _remove_object_ids(objects: list[dict[str, Any]], remove_ids: set[int]) -> None:
    if not remove_ids:
        return
    surviving: list[dict[str, Any]] = []
    for group in objects:
        ids = group.get("ids") or []
        nodes = group.get("nodes") or []
        rotations = group.get("rotations") or []
        levels = group.get("levels") or []
        kept = [
            (object_id, node, rotation, level)
            for object_id, node, rotation, level in zip(ids, nodes, rotations, levels)
            if not (isinstance(object_id, int) and object_id in remove_ids)
        ]
        if not kept:
            continue
        group = dict(group)
        group["ids"] = [row[0] for row in kept]
        group["nodes"] = [row[1] for row in kept]
        group["rotations"] = [row[2] for row in kept]
        group["levels"] = [row[3] for row in kept]
        surviving.append(group)
    objects[:] = surviving


def _clear_town_gate_and_body_intruders(
    objects: list[dict[str, Any]],
    *,
    stock_object_configs: dict[str, dict[str, Any]],
    atlas_width: int,
    atlas_height: int,
) -> dict[str, Any]:
    """Remove scenery/pickups that sit on town bodies or seal town GATE approaches."""
    import sys
    from pathlib import Path

    approach = Path(__file__).resolve().parents[1] / "approach_cell"
    if str(approach) not in sys.path:
        sys.path.insert(0, str(approach))
    import surface_emit as single

    previous_w, previous_h = single.OLDEN_WIDTH, single.OLDEN_HEIGHT
    single.OLDEN_WIDTH = atlas_width
    single.OLDEN_HEIGHT = atlas_height
    try:
        keep_exact = frozenset({"hero-spawner", "random-squad"})
        town_groups = [
            group
            for group in objects
            if isinstance(group.get("sid"), str) and str(group["sid"]).endswith("_city")
        ]
        protected_cells: set[int] = set()
        for group in town_groups:
            sid = str(group["sid"])
            config = stock_object_configs.get(sid)
            if not isinstance(config, dict):
                continue
            for object_id, node, rotation in zip(
                group.get("ids") or [],
                group.get("nodes") or [],
                group.get("rotations") or [],
            ):
                if not isinstance(object_id, int) or not isinstance(node, int):
                    continue
                rot = int(rotation or 0)
                occupied = single.occupied_nodes_for_object_instance(sid, config, node, rot)
                gates = single.gate_nodes_for_object_instance(sid, config, node, rot)
                protected_cells |= set(occupied)
                protected_cells |= set(gates)
                for gate in gates:
                    gx, gy = gate % atlas_width, gate // atlas_width
                    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                        nx, ny = gx + dx, gy + dy
                        if 0 <= nx < atlas_width and 0 <= ny < atlas_height:
                            protected_cells.add(ny * atlas_width + nx)

        remove_ids: set[int] = set()
        examples: list[dict[str, Any]] = []
        for group in objects:
            sid = str(group.get("sid") or "")
            if sid.endswith("_city") or sid in keep_exact or sid.startswith("portal_"):
                continue
            config = stock_object_configs.get(sid)
            if not isinstance(config, dict):
                continue
            for object_id, node, rotation in zip(
                group.get("ids") or [],
                group.get("nodes") or [],
                group.get("rotations") or [],
            ):
                if not isinstance(object_id, int) or not isinstance(node, int):
                    continue
                occupied = single.occupied_nodes_for_object_instance(
                    sid, config, node, int(rotation or 0)
                )
                if occupied & protected_cells:
                    remove_ids.add(object_id)
                    if len(examples) < 40:
                        examples.append({"objectId": object_id, "sid": sid, "node": node})
        _remove_object_ids(objects, remove_ids)
        return {
            "status": "applied",
            "policy": "clear_non_town_intruders_from_city_body_and_gate_cardinals",
            "clearedCount": len(remove_ids),
            "clearedObjectIds": sorted(remove_ids),
            "examples": examples,
        }
    finally:
        single.OLDEN_WIDTH = previous_w
        single.OLDEN_HEIGHT = previous_h


def _h3_faction_mask_bits(factions_mask: Any) -> list[int]:
    if not isinstance(factions_mask, list) or not factions_mask:
        return []
    try:
        low = int(factions_mask[0])
    except (TypeError, ValueError):
        return []
    return [bit for bit in range(16) if (low >> bit) & 1]


# H3 factionsMask bit → stock faction, or None when stock has no counterpart.
H3_FACTION_BIT_TO_STOCK: dict[int, str | None] = {
    0: "human",  # Castle / Temple
    1: "nature",  # Rampart
    2: None,  # Tower
    3: "demon",  # Inferno
    4: "undead",  # Necropolis
    5: "dungeon",  # Dungeon
    6: None,  # Stronghold
    7: None,  # Fortress
    8: None,  # Conflux
    9: None,  # Cove
    10: None,  # Factory
    11: None,  # Bulwark
}


def resolve_stock_faction_choice(
    *,
    factions_mask: Any = None,
    is_faction_random: Any = None,
    already_free_choice: bool = False,
) -> dict[str, Any]:
    """Decide locked stock faction vs lobby free-choice for a town/player.

    Free-choice when:
    - caller already marked freeChoice (random town / unmapped subtype)
    - H3 isFactionRandom
    - mask allows zero or multiple stock factions
    - mask allows any unmappable H3 faction bit
    Locked only when exactly one mappable stock faction bit is set and not random.
    """
    if already_free_choice or is_faction_random is True:
        return {"freeChoice": True, "factionSid": "", "reason": "h3_free_choice_or_unmapped"}
    bits = _h3_faction_mask_bits(factions_mask)
    if not bits:
        return {"freeChoice": True, "factionSid": "", "reason": "empty_factions_mask"}
    mapped: list[str] = []
    unmapped = False
    for bit in bits:
        if bit not in H3_FACTION_BIT_TO_STOCK:
            unmapped = True
            continue
        stock = H3_FACTION_BIT_TO_STOCK[bit]
        if stock is None:
            unmapped = True
        else:
            mapped.append(stock)
    unique = list(dict.fromkeys(mapped))
    if unmapped or len(unique) != 1:
        return {
            "freeChoice": True,
            "factionSid": "",
            "reason": "multi_or_unmapped_factions_mask",
        }
    return {
        "freeChoice": False,
        "factionSid": unique[0],
        "reason": f"forced_factions_mask_{unique[0]}",
    }


def _h3_faction_bits_to_stock(factions_mask: Any) -> str | None:
    """Map a single forced H3 factionsMask bit to a stock factionSid."""
    choice = resolve_stock_faction_choice(factions_mask=factions_mask, is_faction_random=False)
    if choice["freeChoice"]:
        return None
    return str(choice["factionSid"])


def build_vanilla_stock_map(
    *,
    h3m_path: Path,
    stock_core: Path = DEFAULT_STOCK_CORE,
    template_map: Path | None = DEFAULT_STOCK_TEMPLATE_MAP,
    out_dir: Path,
    map_sid: str | None = None,
    install_maps_dir: Path | None = None,
    substitution_table: Path = DEFAULT_SUBSTITUTION_TABLE,
) -> dict[str, Any]:
    if stock_core is None:
        raise VanillaStockEmitError("stock Core.zip path is required")
    if not h3m_path.is_file():
        raise VanillaStockEmitError(f"H3M not found: {h3m_path}")
    if not stock_core.is_file():
        raise VanillaStockEmitError(f"stock Core.zip not found: {stock_core}")
    template_map = resolve_stock_template_map(stock_core, template_map)
    if not template_map.is_file():
        raise VanillaStockEmitError(
            f"stock template map not found beside Core.zip: {template_map} "
            f"(expected StreamingAssets/maps/{STOCK_TEMPLATE_MAP_BASENAME})"
        )

    warnings: list[str] = []

    stock_objects = load_stock_object_ids(stock_core)
    try:
        stock_object_configs = load_stock_object_configs(stock_core)
        allowed_tiles = load_stock_tile_ids(stock_core)
        allowed_waters = load_stock_water_ids(stock_core)
    except (VanillaStockSceneryFootprintError, VanillaStockTerrainError) as ex:
        raise VanillaStockEmitError(str(ex)) from ex
    stock_factions = load_stock_faction_ids(stock_core)
    stock_heroes = load_stock_hero_ids(stock_core)
    _ensure_stock_required_ids(stock_objects)
    try:
        copied_table = load_substitution_table(substitution_table)
    except StockSubstitutionTableError as ex:
        raise VanillaStockEmitError(str(ex)) from ex

    data = read_h3m_bytes(h3m_path)
    try:
        scenario_header = h3m.decode_h3m_scenario_header(data)
    except Exception as ex:
        raise VanillaStockEmitError(f"H3M scenario header decode failed: {ex}") from ex

    title_hint = str((scenario_header.get("title") if isinstance(scenario_header, dict) else None) or h3m_path.stem)
    sid = map_sid or slugify_map_sid(title_hint)
    if sid.startswith("h3_") or "homm3" in sid.lower():
        raise VanillaStockEmitError(f"map SID must not look GE-branded: {sid}")

    try:
        alignment = build_alignment_ir(h3m_path=h3m_path, map_sid=sid)
    except VanillaStockAlignmentError as ex:
        raise VanillaStockEmitError(str(ex)) from ex

    summary = alignment["summary"]
    title = str(alignment["title"])
    entities: list[dict[str, Any]] = list(alignment["globalEntities"])
    layers: list[dict[str, Any]] = list(alignment["layers"])
    layer_ids = [int(layer["index"]) for layer in layers]

    atlas = scenario.build_side_by_side_layer_atlas(
        source_width=int(summary["size"]),
        source_height=int(summary["size"]),
        layer_ids=layer_ids,
        underground_layers=set(layer_ids) - {0},
    )
    arrays = build_empty_atlas_arrays(atlas)
    terrain_stats: dict[str, Any] = {}
    layer_tiles_by_key: dict[str, dict[str, Any]] = {}
    for layer in layers:
        layer_index = int(layer["index"])
        terrain_stats[str(layer_index)] = project_layer_into_atlas(
            layer_tiles=layer["tiles"],
            layer_index=layer_index,
            atlas=atlas,
            out=arrays,
            allowed_tiles=allowed_tiles,
            allowed_waters=allowed_waters,
            apply_underground_tunnel_clearance_flag=True,
        )
        for tile in layer["tiles"]:
            layer_tiles_by_key[str(tile["key"])] = tile
    assert_stock_tile_ids(arrays["tilesMap"], allowed_tiles)
    assert_stock_water_ids(arrays["waterMap"], allowed_waters)
    basin_geometry = apply_stock_ocean_basin_geometry(
        arrays, width=atlas.atlas_width, height=atlas.atlas_height
    )
    envelope_padding = paint_envelope_padding_elevated_dirt(arrays, atlas=atlas)
    assert_stock_tile_ids(arrays["tilesMap"], allowed_tiles)
    assert_stock_water_ids(arrays["waterMap"], allowed_waters)

    objects: list[dict[str, Any]] = []
    omit_counts: Counter[str] = Counter()
    emit_counts: Counter[str] = Counter()
    city_rows: list[dict[str, Any]] = []
    portal_ids: list[int] = []
    random_squad_rows: list[tuple[int, dict[str, Any]]] = []
    random_item_rows: list[dict[str, Any]] = []
    gate_entities: list[dict[str, Any]] = []
    monolith_entities: list[dict[str, Any]] = []
    emitted_mine_object_ids: list[int] = []
    emitted_event_records: list[dict[str, Any]] = []
    unguarded_event_provisional_nodes: dict[int, int] = {}
    guarded_event_host_nodes: dict[int, int] = {}
    scenery_footprint_rows: list[dict[str, Any]] = []
    decisions_by_source_index: dict[int, dict[str, Any]] = {}
    source_object_ids = [int(entity["sourceIndex"]) for entity in entities]
    next_synthetic_object_id = max(source_object_ids, default=-1) + 1

    for entity in entities:
        object_id = int(entity["sourceIndex"])
        layer = int(entity["sourceLayer"])
        x = int(entity["sourceX"])
        y = int(entity["sourceY"])
        record = _entity_as_object_map_record(entity)

        if layer not in atlas.layers or not (0 <= x < atlas.source_width and 0 <= y < atlas.source_height):
            omit_counts["object_anchor_outside_source_envelope_omit_mvp"] += 1
            decisions_by_source_index[object_id] = {
                "action": "outside_envelope",
                "reason": "object_anchor_outside_source_envelope_omit_mvp",
            }
            continue

        biome = terrain_biome_at(layer_tiles_by_key, layer, x, y)
        decision = _table_first_decision(table=copied_table, record=record, stock_objects=stock_objects)
        if decision is None:
            try:
                decision = resolve_object_sid(
                    record,
                    stock_object_ids=stock_objects,
                    terrain_biome=biome,
                )
            except VanillaStockObjectMapError as ex:
                warning = f"omit unsupported object {_entity_key(entity)}: {ex}"
                warnings.append(warning)
                omit_counts["unsupported_object_map_error_omit"] += 1
                decisions_by_source_index[object_id] = {
                    "action": "omit",
                    "reason": "unsupported_object_map_error_omit",
                    "detail": str(ex),
                }
                continue
        decision = _force_stock_travel_decision(record, decision)
        if (
            decision.get("action") == "emit"
            and str(decision.get("sid")) == "random-city"
            and decision.get("kind") == "town"
        ):
            # Random / unmapped towns defer final lock vs free-choice to the owning
            # player's H3 factionsMask / isFactionRandom at spawn emit time.
            decision = dict(decision)
            decision["freeChoice"] = True
            decision["factionSid"] = ""
            decision["reason"] = f"{decision.get('reason')}|defer_player_faction_choice"
        decisions_by_source_index[object_id] = dict(decision)

        if decision.get("action") == "miss":
            reason = str(decision.get("reason") or "copied_substitution_miss")
            omit_counts[reason] += 1
            warnings.append(f"omit unmapped object {_entity_key(entity)}: {reason}")
            continue
        if decision.get("action") == "omit":
            reason = str(decision.get("reason") or "unmapped_object_omit")
            omit_counts[reason] += 1
            warnings.append(f"omit unsupported object {_entity_key(entity)}: {reason}")
            continue
        if decision.get("action") != "emit":
            warning = f"omit object {_entity_key(entity)} with unknown decision {decision!r}"
            warnings.append(warning)
            omit_counts["unknown_object_decision_omit"] += 1
            decisions_by_source_index[object_id] = {
                "action": "omit",
                "reason": "unknown_object_decision_omit",
                "detail": repr(decision),
            }
            continue

        replacement = str(decision["sid"])
        if replacement not in stock_objects:
            warning = (
                f"omit object {_entity_key(entity)}: emit SID not in stock Core: {replacement}"
            )
            warnings.append(warning)
            omit_counts["emit_sid_missing_from_stock_omit"] += 1
            decisions_by_source_index[object_id] = {
                "action": "omit",
                "reason": "emit_sid_missing_from_stock_omit",
                "sid": replacement,
            }
            continue
        if any(token in replacement.lower() for token in FORBIDDEN_SID_SUBSTRINGS):
            warning = f"omit object {_entity_key(entity)}: refusing GE/h3 SID leak: {replacement}"
            warnings.append(warning)
            omit_counts["ge_sid_leak_omit"] += 1
            decisions_by_source_index[object_id] = {
                "action": "omit",
                "reason": "ge_sid_leak_omit",
                "sid": replacement,
            }
            continue
        kind = str(decision.get("kind") or "")
        if kind == "scenery":
            plan_record = dict(record)
            if "expectedSourceBlockCount" in decision:
                plan_record["expectedSourceBlockCount"] = decision["expectedSourceBlockCount"]
            try:
                footprint_plan = plan_stock_scenery(
                    record=plan_record,
                    preferred_sid=replacement,
                    footprint_fill_sid=decision.get("footprintFillSid"),
                    footprint_pathable_sid=decision.get("footprintPathableSid"),
                    configs=stock_object_configs,
                    source_width=atlas.source_width,
                    source_height=atlas.source_height,
                )
            except VanillaStockSceneryFootprintError as ex:
                warning = f"omit scenery {_entity_key(entity)}: {ex}"
                warnings.append(warning)
                omit_counts["scenery_footprint_error_omit"] += 1
                decisions_by_source_index[object_id] = {
                    "action": "omit",
                    "reason": "scenery_footprint_error_omit",
                    "detail": str(ex),
                }
                continue
            placement_rows: list[dict[str, Any]] = []
            for placement_index, placement in enumerate(footprint_plan["placements"]):
                placement_id = object_id if placement_index == 0 else next_synthetic_object_id
                if placement_index != 0:
                    next_synthetic_object_id += 1
                placement_sid = str(placement["sid"])
                placement_node = atlas.target_node(
                    layer, int(placement["sourceX"]), int(placement["sourceY"])
                )
                append_object_instance(objects, placement_sid, placement_id, placement_node)
                emit_counts[placement_sid] += 1
                placement_rows.append(
                    {"id": placement_id, "sid": placement_sid, "node": placement_node, "rotation": 0}
                )
            expected_nodes = sorted(
                atlas.target_node(layer, x + int(dx), y + int(dy))
                for dx, dy in footprint_plan["sourceBlockOffsets"]
                if 0 <= x + int(dx) < atlas.source_width and 0 <= y + int(dy) < atlas.source_height
            )
            scenery_footprint_rows.append(
                {
                    "sourceObjectId": object_id,
                    "sourceKey": _entity_key(entity),
                    "preferredSid": replacement,
                    **{key: value for key, value in footprint_plan.items() if key != "placements"},
                    "expectedNodes": expected_nodes,
                    "placements": placement_rows,
                }
            )
            continue

        town_anchor_evidence: dict[str, Any] | None = None
        if kind == "town" and town_gate_align.is_town_city_sid(replacement):
            try:
                node, town_anchor_evidence = town_gate_align.align_town_emit_node_via_atlas(
                    entity=entity,
                    replacement_sid=replacement,
                    native_config=stock_object_configs[replacement],
                    atlas_target_node=atlas.target_node,
                    layer=layer,
                    atlas_width=atlas.atlas_width,
                    atlas_height=atlas.atlas_height,
                    rotation=0,
                )
            except town_gate_align.TownGateAlignError as ex:
                warning = f"omit town {_entity_key(entity)}: town GATE align failed: {ex}"
                warnings.append(warning)
                omit_counts["town_gate_align_error_omit"] += 1
                decisions_by_source_index[object_id] = {
                    "action": "omit",
                    "reason": "town_gate_align_error_omit",
                    "detail": str(ex),
                }
                continue
            decisions_by_source_index[object_id] = {
                **decisions_by_source_index[object_id],
                "townAnchorEvidence": town_anchor_evidence,
            }
        else:
            node = atlas.target_node(layer, x, y)
        map_event_guard_info: dict[str, Any] | None = None
        if kind == "map_event":
            map_event_guard_info = classify_map_event_guards(record)
            if map_event_guard_info.get("hasGuards"):
                replacement = STOCK_MAP_EVENT_GUARD_SID
                if replacement not in stock_objects:
                    warning = (
                        f"omit map event {_entity_key(entity)}: "
                        f"guard host SID not in stock Core: {replacement}"
                    )
                    warnings.append(warning)
                    omit_counts["map_event_guard_sid_missing_omit"] += 1
                    decisions_by_source_index[object_id] = {
                        "action": "omit",
                        "reason": "map_event_guard_sid_missing_omit",
                        "sid": replacement,
                    }
                    continue
                append_object_instance(objects, replacement, object_id, node)
                emit_counts[replacement] += 1
                guarded_event_host_nodes[object_id] = int(node)
                emitted_event_records.append(record)
            else:
                # Unguarded events become invisible Zone 1x1 markers (not FX / not ObjectConfig).
                unguarded_event_provisional_nodes[object_id] = int(node)
                emitted_event_records.append(record)
                emit_counts[STOCK_MAP_EVENT_MARKER_SID] += 1
                decisions_by_source_index[object_id] = {
                    **decisions_by_source_index[object_id],
                    "host": STOCK_MAP_EVENT_MARKER_SID,
                    "hostMode": "invisible_zone_marker",
                }
                continue
        else:
            append_object_instance(objects, replacement, object_id, node)
            emit_counts[replacement] += 1

        if kind == "town":
            free_choice = bool(decision.get("freeChoice"))
            faction = str(decision.get("factionSid") or "")
            if free_choice:
                faction = ""
            elif not faction or faction not in stock_factions:
                warning = (
                    f"omit town {_entity_key(entity)}: "
                    f"no stock faction parallel for {faction!r}"
                )
                warnings.append(warning)
                removed_sid = remove_object_instance(objects, object_id)
                if removed_sid:
                    emit_counts[removed_sid] -= 1
                    if emit_counts[removed_sid] <= 0:
                        del emit_counts[removed_sid]
                omit_counts["town_faction_unsupported_omit"] += 1
                decisions_by_source_index[object_id] = {
                    "action": "omit",
                    "reason": "town_faction_unsupported_omit",
                    "factionSid": faction,
                }
                continue
            # Owned free-choice / unmapped towns stay random-city so the lobby can pick.
            if free_choice and replacement != "random-city":
                warning = (
                    f"free-choice town {_entity_key(entity)} remapped "
                    f"{replacement!r} → random-city"
                )
                warnings.append(warning)
                removed_sid = remove_object_instance(objects, object_id)
                if removed_sid:
                    emit_counts[removed_sid] -= 1
                    if emit_counts[removed_sid] <= 0:
                        del emit_counts[removed_sid]
                replacement = "random-city"
                append_object_instance(objects, replacement, object_id, node)
                emit_counts[replacement] += 1
                decisions_by_source_index[object_id] = {
                    **decisions_by_source_index[object_id],
                    "sid": replacement,
                    "reason": f"{decision.get('reason')}|force_random_city_free_choice",
                }
            city_rows.append(
                {
                    "type": 0,
                    "id": object_id,
                    "isDefined": True,
                    "factionSid": faction,
                    "spawnHero": True,
                    "buildingsConstructionSid": CITY_BUILDINGS_CONSTRUCTION_SID,
                    "buildingsBanSid": CITY_BUILDINGS_BAN_SID,
                    "buildingsSettingsSid": CITY_BUILDINGS_SETTINGS_SID,
                    "customCityName": "",
                    "_owner": h3_owner_to_olden(entity.get("owner")),
                    "_sourcePosition": {"x": x, "y": y, "z": layer},
                    "_townAnchorEvidence": town_anchor_evidence,
                    "_freeChoice": free_choice,
                }
            )
        if kind == "portal" or replacement.startswith("portal_"):
            portal_ids.append(object_id)
            if replacement == STOCK_SUBTERRANEAN_GATE_SID:
                gate_entities.append(
                    {
                        "index": object_id,
                        "sourceIndex": object_id,
                        "templateObjectId": h3obj.OBJECT_SUBTERRANEAN_GATE,
                        "layer": layer,
                        "sourceLayer": layer,
                        "x": x,
                        "y": y,
                        "sourceX": x,
                        "sourceY": y,
                        "sourceKey": _entity_key(entity),
                        "key": _entity_key(entity),
                        "templateAnimation": entity.get("templateAnimation"),
                        "payloadKind": "subterranean_gate",
                        "category": "subterranean_gate",
                    }
                )
            elif int(record.get("templateObjectId") or -1) == h3obj.OBJECT_TWO_WAY_MONOLITH:
                monolith_entities.append(
                    {
                        "index": object_id,
                        "sourceIndex": object_id,
                        "templateObjectId": h3obj.OBJECT_TWO_WAY_MONOLITH,
                        "templateSubtype": record.get("templateSubtype"),
                        "layer": layer,
                        "sourceLayer": layer,
                        "x": x,
                        "y": y,
                        "sourceX": x,
                        "sourceY": y,
                        "sourceKey": _entity_key(entity),
                        "key": _entity_key(entity),
                        "templateAnimation": entity.get("templateAnimation"),
                        "payloadKind": "two_way_monolith",
                        "category": "two_way_monolith",
                    }
                )
        if kind == "map_event":
            if not (map_event_guard_info and map_event_guard_info.get("hasGuards")):
                raise VanillaStockEmitError(
                    f"unguarded map event reached object emit path at {_entity_key(entity)}"
                )
            if replacement != STOCK_MAP_EVENT_GUARD_SID:
                raise VanillaStockEmitError(
                    f"guarded map event host mismatch at {_entity_key(entity)}: "
                    f"expected {STOCK_MAP_EVENT_GUARD_SID}, got {replacement}"
                )
        elif kind == "random_squad" or replacement == "random-squad":
            # Guarded map events use random-squad but own propRandomSquads via apply_map_events.
            if kind != "map_event":
                random_squad_rows.append((object_id, entity))
        if replacement == "random-item":
            try:
                rarity = _stock_random_item_rarity(entity)
            except (TypeError, ValueError) as ex:
                warning = f"omit random-item {_entity_key(entity)}: {ex}"
                warnings.append(warning)
                removed_sid = remove_object_instance(objects, object_id)
                if removed_sid:
                    emit_counts[removed_sid] -= 1
                    if emit_counts[removed_sid] <= 0:
                        del emit_counts[removed_sid]
                omit_counts["random_item_rarity_omit"] += 1
                decisions_by_source_index[object_id] = {
                    "action": "omit",
                    "reason": "random_item_rarity_omit",
                    "detail": str(ex),
                }
                continue
            random_item_rows.append({"type": 0, "id": object_id, "rarity": rarity})
        if kind == "mine":
            emitted_mine_object_ids.append(object_id)

    town_clear_report = _clear_town_gate_and_body_intruders(
        objects,
        stock_object_configs=stock_object_configs,
        atlas_width=atlas.atlas_width,
        atlas_height=atlas.atlas_height,
    )
    town_cleared_ids = set(town_clear_report.get("clearedObjectIds") or [])
    if town_cleared_ids:
        for object_id in town_cleared_ids:
            decisions_by_source_index[object_id] = {
                "action": "omit",
                "reason": "town_gate_or_body_intruder_cleared",
                "kind": "cleared",
            }
            omit_counts["town_gate_or_body_intruder_cleared"] += 1

    pairing: dict[str, Any] = {"pairCount": 0, "pairs": [], "status": "no_subterranean_gates"}
    monolith_pairing: dict[str, Any] = {"pairCount": 0, "pairs": [], "status": "no_two_way_monoliths"}
    portal_targets: dict[int, int] = {}
    if gate_entities:
        pairing = scenario.pair_subterranean_gates_by_nearest_cross_layer_rule(gate_entities)
        for pair in pairing["pairs"]:
            a = int(pair["surfaceObjectId"])
            b = int(pair["undergroundObjectId"])
            portal_targets[a] = b
            portal_targets[b] = a
    if monolith_entities:
        if scenario_header["version"] == h3m.H3M_VERSION_HOTA:
            monolith_pairing = scenario.pair_two_way_monoliths_by_subtype_cross_layer_rule(
                monolith_entities
            )
        else:
            monolith_pairing = scenario.pair_two_way_monoliths_by_animation_same_layer_rule(
                monolith_entities
            )
        for pair in monolith_pairing["pairs"]:
            portal_targets[int(pair["objectIdA"])] = int(pair["objectIdB"])
    pairing = dict(pairing)
    pairing["monolithRouteContract"] = monolith_pairing

    # Late pass: gate-face rotation against final occupancy (stock-safe).
    try:
        gate_face_report = apply_stock_gate_face_rotations(
            objects,
            stock_object_configs,
            tiles_map=arrays["tilesMap"],
            water_map=arrays["waterMap"],
            levels_map=arrays["levelsMap"],
            climbs_map=arrays["climbsMap"],
            width=atlas.atlas_width,
            height=atlas.atlas_height,
            clearable_object_ids={
                int(placement["id"])
                for row in scenery_footprint_rows
                for placement in (row.get("placements") or [])
                if isinstance(placement, dict) and isinstance(placement.get("id"), int)
            },
            relocation_region_width=atlas.layer_width,
        )
    except (VanillaStockGateFaceError, ValueError, RuntimeError) as ex:
        raise VanillaStockEmitError(f"gate-face rotation failed: {ex}") from ex
    assert_stock_tile_ids(arrays["tilesMap"], allowed_tiles)
    assert_stock_water_ids(arrays["waterMap"], allowed_waters)

    cleared_ids: set[int] = set(town_cleared_ids)
    for row in gate_face_report.get("rotated") or []:
        if not isinstance(row, dict):
            continue
        for cleared in (row.get("clearedApproachBlockers") or []):
            if isinstance(cleared, dict) and isinstance(cleared.get("objectId"), int):
                cleared_ids.add(int(cleared["objectId"]))
        for cleared in (row.get("nonkeeperCleared") or []):
            if isinstance(cleared, dict) and isinstance(cleared.get("objectId"), int):
                cleared_ids.add(int(cleared["objectId"]))
        for cleared in (row.get("clearedRelocationBlockers") or []):
            if isinstance(cleared, dict) and isinstance(cleared.get("objectId"), int):
                cleared_ids.add(int(cleared["objectId"]))

    footprint_prune = _prune_scenery_footprints_after_gate_face(
        scenery_footprint_rows,
        cleared_ids=cleared_ids,
        stock_object_configs=stock_object_configs,
        atlas_width=atlas.atlas_width,
        atlas_height=atlas.atlas_height,
    )
    gate_face_report = dict(gate_face_report)
    gate_face_report["sceneryFootprintPrune"] = footprint_prune
    gate_face_report["townGateBodyClear"] = town_clear_report

    ground_truth = build_placement_ground_truth(
        entities=entities,
        objects=objects,
        decisions_by_source_index=decisions_by_source_index,
        native_object_configs=stock_object_configs,
        atlas_width=atlas.atlas_width,
        atlas_height=atlas.atlas_height,
        approach_cleared_ids=cleared_ids,
    )

    container = poc.read_olden_map_container(template_map)
    if len(container.chunks) < 2 or not all(isinstance(chunk, dict) for chunk in container.chunks[:2]):
        raise VanillaStockEmitError("stock template map chunks are malformed")
    chunks = json.loads(json.dumps(container.chunks, ensure_ascii=False))
    meta = chunks[0]
    map_data = chunks[1]
    props = empty_object_properties(map_data.get("objectsProperties") or {})
    template_rivers = map_data.get("rivers")
    if (
        not isinstance(template_rivers, list)
        or not template_rivers
        or not isinstance(template_rivers[0], dict)
        or not isinstance(template_rivers[0].get("randomSeed"), int)
    ):
        raise VanillaStockEmitError("stock template map must expose a river randomSeed")
    river_seed = int(template_rivers[0]["randomSeed"])
    scenario_players_by_olden_owner = {
        int(row["index"]) + 1: row
        for row in (scenario_header.get("players") or [])
        if isinstance(row, dict) and isinstance(row.get("index"), int) and row.get("playable")
    }
    spawn_build_rows: list[dict[str, Any]] = []
    taken_heroes: list[str] = []

    spawn_city_id_by_owner: dict[int, int] = {}
    spawn_selection_rows: list[dict[str, Any]] = []
    for owner, scenario_player in scenario_players_by_olden_owner.items():
        owned_cities = [city for city in city_rows if city.get("_owner") == owner]
        if not owned_cities:
            raise VanillaStockEmitError(f"playable H3 scenario player has no owned town for spawn: {owner}")
        main_town = scenario_player.get("mainTown")
        if isinstance(main_town, dict):
            expected_position = {
                # H3 stores the main-town entrance two cells left of the placed
                # town object's binary anchor.
                "x": int(main_town["x"]) + 2,
                "y": int(main_town["y"]),
                "z": int(main_town["z"]),
            }
            candidates = [
                city for city in owned_cities if city.get("_sourcePosition") == expected_position
            ]
            if len(candidates) != 1:
                raise VanillaStockEmitError(
                    f"H3 owner {owner} main town {expected_position} matched "
                    f"{len(candidates)} owned town objects"
                )
            selection_reason = "h3_designated_main_town"
        elif len(owned_cities) == 1:
            candidates = owned_cities
            selection_reason = "only_owned_town"
        else:
            # Olden requires exactly one lobby spawn per player, while H3 permits
            # a player to own several towns without designating any as the main
            # town. Object-table order is the only stable source ordering here.
            candidates = [min(owned_cities, key=lambda city: int(city["id"]))]
            selection_reason = "lowest_source_object_id_when_h3_has_no_main_town"
        selected_id = int(candidates[0]["id"])
        spawn_city_id_by_owner[owner] = selected_id
        spawn_selection_rows.append(
            {
                "owner": owner,
                "cityObjectId": selected_id,
                "sourcePosition": candidates[0].get("_sourcePosition"),
                "ownedTownCount": len(owned_cities),
                "reason": selection_reason,
            }
        )

    town_anchor_rows: list[dict[str, Any]] = []
    for city in city_rows:
        owner = city.pop("_owner")
        city.pop("_sourcePosition", None)
        town_evidence = city.pop("_townAnchorEvidence", None)
        free_choice = bool(city.pop("_freeChoice", False))
        if isinstance(town_evidence, dict):
            town_anchor_rows.append(
                {
                    "cityObjectId": int(city["id"]),
                    "factionSid": city.get("factionSid"),
                    "freeChoice": free_choice,
                    **town_evidence,
                }
            )
        props["propCities"].append(city)
        if owner is None:
            continue
        if int(city["id"]) != spawn_city_id_by_owner[int(owner)]:
            city["spawnHero"] = False
            continue
        scenario_player = scenario_players_by_olden_owner.get(int(owner))
        if scenario_player is None:
            raise VanillaStockEmitError(f"city owner is not a playable H3 scenario player: {owner}")
        spawn_type = 0 if scenario_player.get("canHuman") else 1
        if not scenario_player.get("canHuman") and not scenario_player.get("canComputer"):
            raise VanillaStockEmitError(f"playable H3 scenario player cannot be human or computer: {owner}")

        # Fixed stock towns stay locked. Random/unmapped towns follow the owning
        # player's H3 faction availability (free-choice, or a single forced stock bit).
        if free_choice:
            player_choice = resolve_stock_faction_choice(
                factions_mask=scenario_player.get("factionsMask"),
                is_faction_random=scenario_player.get("isFactionRandom"),
                already_free_choice=False,
            )
            free_choice = bool(player_choice["freeChoice"])
            if free_choice:
                # Native editable city choice contract (Story_maps/t_M2 + lobby
                # SlotModel defaults): undefined city/hero metadata. Keeping
                # isCityDefined=true fixes the slot to its current value, so an
                # empty faction merely becomes a locked Random selection.
                city["isDefined"] = False
                city["factionSid"] = ""
                city["spawnHero"] = False
            else:
                city["factionSid"] = str(player_choice["factionSid"])

        if free_choice:
            spawn_build_rows.append(
                {
                    "spawnId": int(city["id"]),
                    "owner": owner,
                    "spawnType": spawn_type,
                    "faction": "",
                    "heroSid": "",
                    "freeChoice": True,
                }
            )
            continue
        faction = str(city["factionSid"])
        if faction not in stock_factions:
            raise VanillaStockEmitError(f"locked town faction not in stock Core: {faction!r}")
        hero_sid = DEFAULT_STOCK_HERO_BY_FACTION.get(faction)
        if hero_sid is None or hero_sid not in stock_heroes:
            raise VanillaStockEmitError(f"default stock hero missing for faction {faction}: {hero_sid}")
        candidates = [hero_sid] + sorted(
            hero for hero in stock_heroes if hero.startswith(hero_sid.rsplit("_", 1)[0] + "_")
        )
        hero_sid = next((candidate for candidate in candidates if candidate not in taken_heroes), None)
        if hero_sid is None:
            raise VanillaStockEmitError(f"no unused stock heroes left for faction {faction}")
        taken_heroes.append(hero_sid)
        spawn_build_rows.append(
            {
                "spawnId": int(city["id"]),
                "owner": owner,
                "spawnType": spawn_type,
                "faction": faction,
                "heroSid": hero_sid,
                "freeChoice": False,
            }
        )

    # Human-capable spawns first so the lobby binds the player to the intended start town/faction.
    spawn_build_rows.sort(key=lambda row: (int(row["spawnType"]), int(row["owner"])))
    spawn_meta_rows: list[dict[str, Any]] = []
    taken_heroes = [str(row["heroSid"]) for row in spawn_build_rows if not row.get("freeChoice") and row.get("heroSid")]
    for row in spawn_build_rows:
        spawn_id = int(row["spawnId"])
        owner = int(row["owner"])
        spawn_type = int(row["spawnType"])
        free_choice = bool(row.get("freeChoice"))
        faction = str(row["faction"])
        hero_sid = str(row["heroSid"])
        props["propSpawns"].append(
            {
                "type": 0,
                "id": spawn_id,
                "owner": owner,
                "spawnType": spawn_type,
                "spawnPointType": 0,
                "isLocked": False,
            }
        )
        if free_choice:
            # Lobby free-choice. qmq() maps both false definition flags to
            # SlotModel's editable random defaults. Empty SIDs mirror t_M2.
            spawn_meta_rows.append(
                {
                    "owner": owner,
                    "spawnType": spawn_type,
                    "playerId": "",
                    "spawnPointType": 0,
                    "isCityDefined": False,
                    "factionSid": "",
                    "isHeroDefined": False,
                    "heroSid": "",
                    "colorId": -1,
                    "isAlive": True,
                    "isLocked": False,
                }
            )
            continue
        props["propHeroes"].append({"type": 0, "id": spawn_id, "isDefined": True, "heroSid": hero_sid})
        spawn_meta_rows.append(
            {
                "owner": owner,
                "spawnType": spawn_type,
                "playerId": "",
                "spawnPointType": 0,
                "isCityDefined": True,
                "factionSid": faction,
                "isHeroDefined": True,
                "heroSid": hero_sid,
                "colorId": -1,
                "isAlive": True,
                "isLocked": False,
            }
        )

    for portal_id in portal_ids:
        props["propPortals"].append(
            {"type": 0, "id": portal_id, "targetIdx": portal_targets.get(portal_id, -1), "isActive": True}
        )
    surviving_object_ids = {
        int(object_id)
        for group in objects
        for object_id in (group.get("ids") or [])
        if isinstance(object_id, int)
    }
    for row in random_item_rows:
        if int(row["id"]) not in surviving_object_ids:
            continue
        props["propRandomItems"].append(row)
    for squad_id, squad_entity in random_squad_rows:
        if squad_id not in surviving_object_ids:
            continue
        try:
            requested = stock_random_squad_requested_value(squad_entity)
            reaction = stock_monster_reaction_type(squad_entity)
        except VanillaStockVictoryError as ex:
            raise VanillaStockEmitError(str(ex)) from ex
        props["propRandomSquads"].append(
            stock_random_squad_property_row(
                squad_id,
                requested_value=requested,
                reaction_type=reaction,
                never_flees=bool(squad_entity.get("neverFlees")),
                not_growing=bool(squad_entity.get("notGrowingTeam")),
            )
        )

    desc = summary.get("description") or f"Vanilla stock translation of {title}"
    map_hash = hashlib.md5(sid.encode("utf-8")).hexdigest()
    total = atlas.atlas_width * atlas.atlas_height
    next_free = max((int(object_id) for group in objects for object_id in group["ids"]), default=-1) + 1
    meta.update(
        {
            "title": title,
            "template": "",
            "desc": desc,
            "displayWinCondition": "",
            "hashSum": map_hash,
            "nameFromLocalization": False,
            "descFromLocalization": False,
            "sizeX": atlas.atlas_width,
            "sizeZ": atlas.atlas_height,
            "isScenario": True,
            "gameMode": 0,
            "endController": 0,
            "banInfoData": {key: [] for key in ("bannedMagics", "bannedItems", "bannedSkills", "bannedHeroes", "bannedUnits")},
            "spawns": {
                "playersCount": len(spawn_meta_rows),
                "spawns": spawn_meta_rows,
                "takenHeroes": taken_heroes,
            },
            "takenHeroes": taken_heroes,
        }
    )
    map_data.update(
        {
            "fileMapName": sid,
            "mapName": title,
            "mapNameFromLoc": False,
            "mapDesc": desc,
            "mapDescFromLoc": False,
            "sizeX_": atlas.atlas_width,
            "sizeZ_": atlas.atlas_height,
            "levelsMap": arrays["levelsMap"],
            "climbsMap": arrays["climbsMap"],
            "roadsMap": arrays["roadsMap"],
            "waterMap": arrays["waterMap"],
            "tilesMap": arrays["tilesMap"],
            "customAreasPainting": arrays["customAreasPainting"],
            "haveCustomAreas": False,
            "areasVersion": 1,
            "objects": objects,
            "objectsFreeId": next_free,
            "objectsProperties": props,
            "squads": [],
            "squadsFreeId": 0,
            "markers": [],
            "markersFreeId": 0,
            "takenHeroes": taken_heroes,
            "keyObjects": [],
            "objectValuesOverrides": {"list": []},
            "areas": [{"id": 0, "keyObjectId": -1, "rootNode": 0, "nodes": list(range(total))}],
            "rivers": [{"sid": "", "randomSeed": river_seed, "nodes": []}],
            "generatorChecksum": "",
            "views": [single_view_for_atlas(atlas)],
            "banInfoData": meta["banInfoData"],
            "isScenario": True,
            "gameMode": 0,
            "endController": 0,
        }
    )
    settings = map_data.get("settings") if isinstance(map_data.get("settings"), dict) else {}
    settings["mapWinConditions"] = []
    settings["isScenario"] = True
    map_data["settings"] = settings

    source_mines = source_mine_records(
        [_entity_as_object_map_record(entity) for entity in entities]
    )
    try:
        victory_info = apply_victory_contract(
            header=scenario_header,
            map_title=title,
            meta=meta,
            map_data=map_data,
            props=props,
            emitted_mine_object_ids=emitted_mine_object_ids,
            source_mine_record_count=len(source_mines),
        )
        if victory_info.get("warning"):
            warnings.append(str(victory_info["warning"]))
        occupied_for_events = _occupied_nodes_for_event_relocation(
            objects,
            stock_object_configs=stock_object_configs,
            atlas_width=atlas.atlas_width,
            atlas_height=atlas.atlas_height,
        )
        try:
            event_info = apply_map_events(
                map_sid=sid,
                map_title=title,
                event_records=emitted_event_records,
                props=props,
                provisional_nodes=unguarded_event_provisional_nodes,
                host_nodes=guarded_event_host_nodes,
                levels_map=list(arrays["levelsMap"]),
                climbs_map=list(arrays["climbsMap"]),
                occupied_nodes=occupied_for_events,
                envelope_nodes=_envelope_nodes_for_atlas(atlas),
                atlas_width=atlas.atlas_width,
                atlas_height=atlas.atlas_height,
                layer_width=atlas.source_width,
                first_marker_id=1,
            )
        except VanillaStockVictoryError as ex:
            warnings.append(f"map events omitted: {ex}")
            event_info = {
                "eventCount": 0,
                "unguardedCount": 0,
                "guardedCount": 0,
                "giveResActionCount": 0,
                "removeResActionCount": 0,
                "spawnMapObjectActionCount": 0,
                "omittedRewardGaps": [],
                "notes": [str(ex)],
                "quests": [],
                "counters": [],
                "dialogs": [],
                "decoPlacements": [],
            }
        try:
            timed_info = apply_global_timed_events(
                map_sid=sid,
                map_title=title,
                global_timed_events=(alignment.get("globalTimedEvents") or None),
            )
        except VanillaStockVictoryError as ex:
            warnings.append(f"timed events omitted: {ex}")
            timed_info = {
                "briefingCount": 0,
                "timedGrantCount": 0,
                "omittedGaps": [str(ex)],
                "notes": [str(ex)],
                "quests": [],
                "counters": [],
                "dialogDocuments": [],
            }
    except (VanillaStockVictoryError, KeyError, TypeError, ValueError) as ex:
        raise VanillaStockEmitError(str(ex)) from ex
    if len(chunks) < 4 or not isinstance(chunks[3], dict):
        chunks.append({"comment": "", "aiRolesId": "", "counters": [], "interruptions": [], "quests": []})
    quest_chunk = chunks[3]
    event_counters = list(event_info.get("counters") or [])
    event_quests = list(event_info.get("quests") or [])
    timed_counters = list(timed_info.get("counters") or [])
    timed_quests = list(timed_info.get("quests") or [])
    quest_chunk["comment"] = (
        f"vanilla_stock victory={victory_info['mode']}; "
        f"map_events={event_info.get('eventCount', 0)}; "
        f"timed_briefings={timed_info.get('briefingCount', 0)}"
    )
    quest_chunk["counters"] = (
        list(victory_info.get("counters") or []) + event_counters + timed_counters
    )
    quest_chunk["quests"] = list(victory_info.get("quests") or []) + event_quests + timed_quests
    if not isinstance(quest_chunk.get("interruptions"), list):
        quest_chunk["interruptions"] = []

    event_markers = list(event_info.get("markers") or [])
    map_data["markers"] = event_markers
    map_data["markersFreeId"] = (
        max((int(row["id"]) for row in event_markers if isinstance(row.get("id"), int)), default=0) + 1
    )

    # Decorative gold marks near unguarded Zones (findable, non-interactive FX).
    next_object_id = int(map_data.get("objectsFreeId") or next_free)
    for deco in event_info.get("decoPlacements") or []:
        deco_id = next_object_id
        next_object_id += 1
        append_object_instance(
            objects,
            STOCK_MAP_EVENT_DECO_SID,
            deco_id,
            int(deco["decoNode"]),
        )
        emit_counts[STOCK_MAP_EVENT_DECO_SID] += 1
        deco["decoObjectId"] = deco_id
    map_data["objects"] = objects
    map_data["objectsFreeId"] = next_object_id
    map_data["objectsProperties"] = props

    all_dialog_docs = list(event_info.get("dialogDocuments") or []) + list(
        timed_info.get("dialogDocuments") or []
    )
    optional_overlay_dir: Path | None = None
    core_dialog_install_reports: list[dict[str, Any]] = []
    if all_dialog_docs:
        optional_overlay_dir = out_dir / "optional_core_overlay_for_events"
        for doc_row in all_dialog_docs:
            target = optional_overlay_dir / str(doc_row["relativeMember"])
            write_json(target, doc_row["document"])
        write_json(
            out_dir / f"{sid}.events_optional_core_readme.json",
            {
                "coreDialogInstallRequired": True,
                "overlayDir": str(optional_overlay_dir),
                "omittedRewardGaps": event_info.get("omittedRewardGaps") or [],
                "timedOmittedGaps": timed_info.get("omittedGaps") or [],
                "notes": list(event_info.get("notes") or []) + list(timed_info.get("notes") or []),
                "howToInstall": (
                    "Merge optional_core_overlay_for_events/DB into the game Core.zip "
                    "(tools/install_vanilla_stock_event_dialog_overlay.py)."
                ),
            },
        )

    if optional_overlay_dir is not None:
        import sys as _sys

        tools_dir = Path(__file__).resolve().parents[2] / "tools"
        if str(tools_dir) not in _sys.path:
            _sys.path.insert(0, str(tools_dir))
        from install_vanilla_stock_event_dialog_overlay import install_dialog_overlay

        cores_to_patch = [stock_core]
        if install_maps_dir is not None:
            candidate = install_maps_dir.parent / "Core.zip"
            if candidate.is_file() and candidate.resolve() != stock_core.resolve():
                cores_to_patch.append(candidate)
        for core_path in cores_to_patch:
            core_dialog_install_reports.append(
                install_dialog_overlay(overlay_dir=optional_overlay_dir, core_zip=core_path)
            )

    out_map = out_dir / "maps" / f"{sid}.map"
    poc.write_olden_map_container(
        out_map,
        poc.OldenMapContainer(container.version, container.prefix_suffix, chunks),
        map_hash,
    )
    installed_path = install_maps_dir / f"{sid}.map" if install_maps_dir is not None else None

    emitted_sids = sorted({str(group["sid"]) for group in objects})
    ge_leaks = [sid_value for sid_value in emitted_sids if any(token in sid_value.lower() for token in FORBIDDEN_SID_SUBSTRINGS)]
    if ge_leaks:
        raise VanillaStockEmitError(f"GE SID leaks detected after emit: {ge_leaks}")
    missing_stock = [sid_value for sid_value in emitted_sids if sid_value not in stock_objects]
    if missing_stock:
        raise VanillaStockEmitError(f"emitted SIDs absent from stock Core: {missing_stock}")

    ground_truth_path = out_dir / f"{sid}.placement_ground_truth.json"
    write_json(ground_truth_path, ground_truth)
    alignment_path = out_dir / f"{sid}.alignment_ir.json"
    # Drop bulky walk/layer tile dumps from the on-disk IR breadcrumb.
    alignment_breadcrumb = {
        key: value
        for key, value in alignment.items()
        if key not in {"layers", "walkRecords", "manifestRecords"}
    }
    write_json(alignment_path, alignment_breadcrumb)

    ug_tunnel = []
    for layer_stat in terrain_stats.values():
        clearance = (layer_stat or {}).get("undergroundTunnelClearance")
        if isinstance(clearance, dict):
            ug_tunnel.append(clearance)

    manifest = {
        "schema": SCHEMA_MAP,
        "status": STATUS,
        "pipeline": PIPELINE,
        "mapSid": sid,
        "title": title,
        "sourceH3m": str(h3m_path),
        "sourceTitle": summary.get("title"),
        "sourceSize": summary.get("size"),
        "sourceLayers": summary.get("layers"),
        "stockCore": str(stock_core),
        "templateMap": str(template_map),
        "warnings": warnings,
        "outputMap": str(out_map),
        "installedMap": str(installed_path) if installed_path else None,
        "substitutionTable": copied_table.manifest(),
        "atlas": atlas.as_manifest(),
        "viewsCount": 1,
        "stockAllowlists": {
            "tileIds": sorted(allowed_tiles),
            "waterIds": sorted(allowed_waters),
            "tileSource": "DB/map/tiles/tiles.json",
            "waterSource": "DB/map/waters/waters.json",
        },
        "terrain": {
            "policy": terrain_policy_manifest(),
            "nativeOceanBasinGeometry": basin_geometry,
            "envelopePadding": envelope_padding,
            "statsByLayer": terrain_stats,
            "undergroundTunnelClearance": ug_tunnel,
            "tileHistogram": dict(sorted(Counter(arrays["tilesMap"]).items())),
            "waterHistogram": dict(sorted(Counter(arrays["waterMap"]).items())),
            "levelsHistogram": dict(sorted(Counter(arrays["levelsMap"]).items())),
            "climbsHistogram": dict(sorted(Counter(arrays["climbsMap"]).items())),
        },
        "gateFaceRotation": gate_face_report,
        "placementGroundTruth": {
            "schema": ground_truth.get("schema"),
            "status": "applied",
            "artifact": str(ground_truth_path),
            "stats": ground_truth.get("stats"),
        },
        "alignmentIr": {
            "schema": alignment.get("schema"),
            "artifact": str(alignment_path),
            "entityCount": len(entities),
            "sourceLayerCounts": (alignment.get("source") or {}).get("sourceLayerCounts"),
        },
        "sceneryFootprints": {
            "schema": SCENERY_FOOTPRINT_SCHEMA,
            "policy": SCENERY_FOOTPRINT_POLICY,
            "result": "PASS",
            "sourceObjectCount": len(scenery_footprint_rows),
            "placementCount": sum(len(row["placements"]) for row in scenery_footprint_rows),
            "catalogExactCount": sum(row["mode"] == "catalog_exact" for row in scenery_footprint_rows),
            "stock1x1TiledCount": sum(row["mode"] == "stock_1x1_tiled" for row in scenery_footprint_rows),
            "stockPathableDecorationCount": sum(
                row["mode"] == "stock_pathable_decoration" for row in scenery_footprint_rows
            ),
            "clippedSourceBlockCount": sum(row["clippedSourceBlockCount"] for row in scenery_footprint_rows),
            "mismatchCount": 0,
            "coreOverlayRequired": False,
            "stockNativeLimitation": "residual source blocked cells use visible stock 1x1 scenery because vanilla Core has no invisible blocker",
            "rows": scenery_footprint_rows,
        },
        "objects": {
            "emittedInstanceCount": sum(emit_counts.values()),
            "emittedSidHistogram": dict(sorted(emit_counts.items())),
            "omitReasonHistogram": dict(sorted(omit_counts.items())),
            "cityCount": len(city_rows),
            "townGateAlign": {
                "policy": town_gate_align.POLICY,
                "cityCount": len(town_anchor_rows),
                "rows": town_anchor_rows,
            },
            "portalCount": len(portal_ids),
            "randomSquadCount": len(random_squad_rows),
            "randomItemCount": len(props["propRandomItems"]),
            "mineCount": len(emitted_mine_object_ids),
            "mapEventCount": len(emitted_event_records),
            "subterraneanGatePairing": pairing,
        },
        "scenarioHeader": {
            "victory": scenario_header.get("victory"),
            "loss": scenario_header.get("loss"),
            "playablePlayers": [
                {
                    "index": p["index"],
                    "canHuman": p.get("canHuman"),
                    "canComputer": p.get("canComputer"),
                    "factionsMask": p.get("factionsMask"),
                    "isFactionRandom": p.get("isFactionRandom"),
                }
                for p in (scenario_header.get("players") or [])
                if p.get("playable")
            ],
        },
        "victory": victory_info,
        "events": {
            "eventCount": event_info.get("eventCount"),
            "unguardedCount": event_info.get("unguardedCount"),
            "guardedCount": event_info.get("guardedCount"),
            "markerSid": event_info.get("markerSid"),
            "guardSid": event_info.get("guardSid"),
            "decoSid": event_info.get("decoSid"),
            "placedObjectIds": event_info.get("placedObjectIds"),
            "unguardedObjectIds": event_info.get("unguardedObjectIds"),
            "unguardedMarkerIds": event_info.get("unguardedMarkerIds"),
            "guardedObjectIds": event_info.get("guardedObjectIds"),
            "relocations": event_info.get("relocations") or [],
            "decoPlacements": event_info.get("decoPlacements") or [],
            "omittedRewardGaps": event_info.get("omittedRewardGaps") or [],
            "giveResActionCount": event_info.get("giveResActionCount") or 0,
            "removeResActionCount": event_info.get("removeResActionCount") or 0,
            "spawnMapObjectActionCount": event_info.get("spawnMapObjectActionCount") or 0,
            "coreDialogInstallRequired": bool(all_dialog_docs),
            "notes": list(event_info.get("notes") or []) + list(timed_info.get("notes") or []),
            "optionalOverlayDir": str(optional_overlay_dir) if all_dialog_docs else None,
        },
        "timedEvents": {
            "briefingCount": timed_info.get("briefingCount"),
            "timedGrantCount": timed_info.get("timedGrantCount"),
            "omittedGaps": timed_info.get("omittedGaps") or [],
        },
        "spawns": meta["spawns"],
        "spawnSelection": spawn_selection_rows,
        "coreOverlayEmitted": False,
        "coreOverlayOptionalEventsOnly": bool(all_dialog_docs),
        "coreDialogInstallReports": core_dialog_install_reports,
        "proofBoundary": (
            "generated_artifact_stock_sid_tile_water_gate_face_ground_truth_validated_"
            "runtime_unvalidated"
        ),
        "howToOpen": "Stock Olden Era standalone map browser; runtime behavior remains unvalidated.",
        "knownStockLimitations": [
            "ocean_uses_sand_basin_not_ge_water_tiles_18_22",
            "subterranean_uses_dirt_not_ge_burrow_15",
            "elevated_rock_uses_dirt_levels_1_not_ge_void_23",
            "atlas_envelope_padding_elevated_dirt_no_ramps",
            "boats_and_water_travel_objects_omitted",
            "no_homm3_xfp_exact_footprints",
            "mana_and_unmapped_h3_artifacts_remain_named_omit_gaps",
        ],
    }
    manifest_path = out_dir / f"{sid}.manifest.json"
    write_json(manifest_path, manifest)

    from .validate_map import validate_vanilla_stock_map

    validate_vanilla_stock_map(
        map_path=out_map,
        stock_core=stock_core,
        expect_map_sid=sid,
        manifest_path=manifest_path,
    )
    if installed_path is not None:
        installed_path.parent.mkdir(parents=True, exist_ok=True)
        installed_path.write_bytes(out_map.read_bytes())
        validate_vanilla_stock_map(
            map_path=installed_path,
            stock_core=stock_core,
            expect_map_sid=sid,
            manifest_path=manifest_path,
        )
    return manifest


def main_emit_cli(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--h3m", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--stock-core", type=Path, required=DEFAULT_STOCK_CORE is None, default=DEFAULT_STOCK_CORE)
    parser.add_argument(
        "--template-map",
        type=Path,
        default=DEFAULT_STOCK_TEMPLATE_MAP,
        help=(
            "Optional stock .map shell. Default: StreamingAssets/maps/"
            f"{STOCK_TEMPLATE_MAP_BASENAME} beside Core.zip "
            "(or STOCK_TEMPLATE_MAP env)."
        ),
    )
    parser.add_argument("--map-sid", type=str, default=None)
    parser.add_argument("--install-maps-dir", type=Path, default=None)
    parser.add_argument("--substitution-table", type=Path, default=DEFAULT_SUBSTITUTION_TABLE)
    args = parser.parse_args(argv)
    manifest = build_vanilla_stock_map(
        h3m_path=args.h3m,
        stock_core=args.stock_core,
        template_map=args.template_map,
        out_dir=args.out_dir,
        map_sid=args.map_sid,
        install_maps_dir=args.install_maps_dir,
        substitution_table=args.substitution_table,
    )
    warning_rows = list(manifest.get("warnings") or [])
    summary = {
        "mapSid": manifest["mapSid"],
        "outputMap": manifest["outputMap"],
        "installedMap": manifest["installedMap"],
        "templateMap": manifest.get("templateMap"),
        "warningCount": len(warning_rows),
        "warnings": warning_rows,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_emit_cli())
