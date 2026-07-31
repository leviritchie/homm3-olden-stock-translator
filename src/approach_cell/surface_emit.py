"""Stock-safe footprint / rotation helpers carved from the private surface emit.

Only the symbols needed by ``vanilla_stock`` and ``gate_face_rotation`` live here.
No Golden Era Core overlays, OfflineUnlockMod paths, or campaign localization.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

# Mutable atlas size — callers temporarily assign for the active map envelope.
OLDEN_WIDTH = 72
OLDEN_HEIGHT = 72

OLDEN_DEFAULT_ROTATION = 0
OLDEN_MIRRORED_ROTATION = 10

H3_NEUTRAL_OWNER = 255
OLDEN_SPAWN_TYPE_HUMAN = 0
OLDEN_SPAWN_TYPE_AI = 1
OLDEN_SPAWN_POINT_TYPE_CITY = 0
OLDEN_SPAWN_POINT_TYPE_HERO = 1

# Stock lane never emits these GE water tile ids; gate-face still references the
# constants when evaluating approach freeness against temporary Void markers.
NATIVE_WATER_TILE_CODE = 18
NATIVE_WATER_SHORE_TILE_CODES_BY_EDGE = {
    "north": 19,
    "south": 20,
    "west": 21,
    "east": 22,
}

HOMM3_PATHING_BLOCKER_SID = "homm3_pathing_blocker"
HOMM3_PATHING_GAP_FILL_VISIBLE_SID = "mountain_dirt_small_1"
HOMM3_PATHING_VISUAL_SID_PREFIX = "homm3_pathing_visual"
HOMM3_RANDOM_RESOURCE_PICKUP_SID = "random-resource"
HOMM3_FLOTSAM_PICKUP_SID = "flotsam"

H3M_MASK_WIDTH = 8
H3M_MASK_HEIGHT = 6
H3M_MASK_ANCHOR_X = 7
H3M_MASK_ANCHOR_Y = 5

POINT_ANCHOR_WITHOUT_NATIVE_FOOTPRINT_SIDS = {
    "dirt_stones_1",
    "dirt_stones_2",
    "dirt_strange_flower",
    "dirt_rock_1",
    "dead_stones_1",
    "desert_stones_1",
    "flowers_1",
    "flowers_2",
    "fx_map_light",
    "fx_quest_mark_gold_01",
    "fx_quest_mark_silver_01",
    "grass_1",
    "grass_2",
    "grass_death_1",
    "grass_desert_1",
    "grass_snow_1",
    "grass_snow_2",
    "grass_stones_1",
    "grass_stones_2",
    "lava_stones_1",
    "log_1",
    "mushrooms_1",
    "mushrooms_2",
    "mushrooms_3",
    "mushrooms_4",
    "pool_dead_small_1",
    "snow_stones_1",
    "snow_stones_2",
    "snow_stones_3",
    "snow_stones_4",
    "tree_dead_1",
    "water_reed_1",
}


def is_homm3_pathing_visual_sid(sid: str) -> bool:
    return sid.startswith(f"{HOMM3_PATHING_VISUAL_SID_PREFIX}_")


def is_pickup_range_fallback_sid(sid: str) -> bool:
    if sid.startswith("resource_"):
        return True
    return sid in {
        "chest",
        HOMM3_RANDOM_RESOURCE_PICKUP_SID,
        HOMM3_FLOTSAM_PICKUP_SID,
    }


def h3m_blocked_mask_offsets(mask: Any) -> set[tuple[int, int]]:
    if not isinstance(mask, list) or len(mask) != H3M_MASK_HEIGHT:
        return set()
    offsets: set[tuple[int, int]] = set()
    for row_index, raw in enumerate(mask):
        if not isinstance(raw, int):
            raise ValueError(f"H3M object mask byte is not an integer: {raw!r}")
        if raw < 0 or raw > 255:
            raise ValueError(f"H3M object mask byte outside 0..255: {raw!r}")
        for col_index in range(H3M_MASK_WIDTH):
            if (raw >> col_index) & 1 == 0:
                offsets.add((col_index - H3M_MASK_ANCHOR_X, row_index - H3M_MASK_ANCHOR_Y))
    return offsets


def source_block_offsets(entity: dict[str, Any]) -> set[tuple[int, int]]:
    return h3m_blocked_mask_offsets(entity.get("templateBlockMask"))


def occupied_nodes_for_object_instance(
    sid: str,
    config: dict[str, Any],
    anchor_node: int,
    rotation: int,
) -> set[int]:
    if sid == HOMM3_PATHING_BLOCKER_SID:
        return {anchor_node}
    anchor_x = anchor_node % OLDEN_WIDTH
    anchor_y = anchor_node // OLDEN_WIDTH
    size_x = config.get("sizeX")
    size_z = config.get("sizeZ")
    footprint_source = "native_object_config_size_grid"
    if not isinstance(size_x, int) or not isinstance(size_z, int) or size_x <= 0 or size_z <= 0:
        generator_config = config.get("generatorConfig")
        if (
            isinstance(generator_config, dict)
            and isinstance(generator_config.get("buildingSizeX"), int)
            and isinstance(generator_config.get("buildingSizeZ"), int)
        ):
            size_x = int(generator_config["buildingSizeX"])
            size_z = int(generator_config["buildingSizeZ"])
            footprint_source = "native_generator_building_size_grid"
        elif sid in POINT_ANCHOR_WITHOUT_NATIVE_FOOTPRINT_SIDS or is_homm3_pathing_visual_sid(sid):
            return set()
        else:
            return set()
    nodes = config.get("nodes")
    if nodes is not None and (not isinstance(nodes, list) or len(nodes) != size_x * size_z):
        raise ValueError(f"cannot compute occupied nodes for {sid}: invalid nodes grid {nodes!r}")
    mirrored = rotation == OLDEN_MIRRORED_ROTATION and config.get("canBeMirrored") is True
    occupied: set[int] = set()
    for config_z in range(size_z):
        for config_x in range(size_x):
            value = nodes[config_z * size_x + config_x] if isinstance(nodes, list) else None
            if value != 1:
                continue
            placed_dx = size_x - 1 - config_x if mirrored else config_x
            grid_x = anchor_x + placed_dx
            grid_y = anchor_y + (size_z - 1 - config_z)
            if 0 <= grid_x < OLDEN_WIDTH and 0 <= grid_y < OLDEN_HEIGHT:
                occupied.add(grid_y * OLDEN_WIDTH + grid_x)
    if (
        footprint_source == "native_object_config_size_grid"
        and not occupied
        and sid not in POINT_ANCHOR_WITHOUT_NATIVE_FOOTPRINT_SIDS
        and not is_homm3_pathing_visual_sid(sid)
    ):
        return set()
    return occupied


def gate_nodes_for_object_instance(
    sid: str,
    config: dict[str, Any],
    anchor_node: int,
    rotation: int,
) -> set[int]:
    if sid == HOMM3_PATHING_BLOCKER_SID or is_homm3_pathing_visual_sid(sid):
        return set()
    size_x = config.get("sizeX")
    size_z = config.get("sizeZ")
    nodes = config.get("nodes")
    if (
        not isinstance(size_x, int)
        or not isinstance(size_z, int)
        or size_x <= 0
        or size_z <= 0
        or not isinstance(nodes, list)
        or len(nodes) != size_x * size_z
    ):
        return set()
    anchor_x = anchor_node % OLDEN_WIDTH
    anchor_y = anchor_node // OLDEN_WIDTH
    mirrored = rotation == OLDEN_MIRRORED_ROTATION and config.get("canBeMirrored") is True
    gates: set[int] = set()
    for config_z in range(size_z):
        for config_x in range(size_x):
            if nodes[config_z * size_x + config_x] != 2:
                continue
            placed_dx = size_x - 1 - config_x if mirrored else config_x
            grid_x = anchor_x + placed_dx
            grid_y = anchor_y + (size_z - 1 - config_z)
            if 0 <= grid_x < OLDEN_WIDTH and 0 <= grid_y < OLDEN_HEIGHT:
                gates.add(grid_y * OLDEN_WIDTH + grid_x)
    return gates


# --- Ownership helpers (stock-safe carve from private surface_emit) ---
# Used by vanilla_stock.ownership_contract. No StoryHub/campaign grant paths.

def upsert_property_row_by_id(rows: list[dict[str, Any]], row: dict[str, Any]) -> None:
    object_id = row.get("id")
    if not isinstance(object_id, int):
        raise ValueError(f"property row missing integer id: {row!r}")
    for index, existing in enumerate(rows):
        if isinstance(existing, dict) and existing.get("id") == object_id:
            rows[index] = row
            return
    rows.append(row)

def bind_orphan_ai_owners_to_neutral_towns(
    properties: dict[str, Any],
    entities_by_id: dict[int, dict[str, Any]],
    *,
    human_olden_owner: int,
) -> dict[str, Any]:
    """Give hero-only AI sides a City propSpawn from the nearest unbound neutral town.

    AB/SoD maps often place AI heroes beside fully neutral towns (Mutare / Dragon's Blood).
    StoryHub Bot binding refuses hero-only AI owners, so claim one unused neutral city per
    orphan AI owner (deterministic nearest by source Manhattan distance).
    """
    spawn_rows = list(properties.get("propSpawns") or [])
    city_ids = {
        int(row["id"])
        for row in (properties.get("propCities") or [])
        if isinstance(row, dict) and isinstance(row.get("id"), int)
    }
    if not city_ids:
        return {"boundCount": 0, "bindings": [], "policy": "no_cities"}

    owners_with_city: set[int] = set()
    owners_with_hero: dict[int, list[int]] = defaultdict(list)
    claimed_city_ids: set[int] = set()
    for row in spawn_rows:
        if not isinstance(row, dict):
            continue
        owner = row.get("owner")
        object_id = row.get("id")
        if not isinstance(owner, int) or not isinstance(object_id, int):
            continue
        if row.get("spawnPointType") == OLDEN_SPAWN_POINT_TYPE_CITY:
            owners_with_city.add(owner)
            claimed_city_ids.add(object_id)
        elif row.get("spawnPointType") == OLDEN_SPAWN_POINT_TYPE_HERO:
            owners_with_hero[owner].append(object_id)

    orphan_owners = sorted(
        owner
        for owner in owners_with_hero
        if owner != human_olden_owner and owner not in owners_with_city
    )
    if not orphan_owners:
        return {"boundCount": 0, "bindings": [], "policy": "no_orphan_ai_owners"}

    available_cities: list[tuple[int, int, int]] = []
    for object_id in sorted(city_ids):
        if object_id in claimed_city_ids:
            continue
        entity = entities_by_id.get(object_id)
        if entity is None:
            continue
        owner = entity.get("owner")
        if isinstance(owner, int) and owner != H3_NEUTRAL_OWNER:
            continue
        x = int(entity.get("sourceX") if entity.get("sourceX") is not None else entity.get("x") or 0)
        y = int(entity.get("sourceY") if entity.get("sourceY") is not None else entity.get("y") or 0)
        available_cities.append((object_id, x, y))
    if len(available_cities) < len(orphan_owners):
        raise ValueError(
            f"orphan AI owners {orphan_owners} need City propSpawns but only "
            f"{len(available_cities)} unbound neutral towns remain"
        )

    bindings: list[dict[str, Any]] = []
    used_cities: set[int] = set()
    for olden_owner in orphan_owners:
        hero_ids = sorted(owners_with_hero[olden_owner])
        # Anchor distance on the lowest hero object id for this owner.
        anchor = entities_by_id.get(hero_ids[0], {})
        ax = int(anchor.get("sourceX") if anchor.get("sourceX") is not None else anchor.get("x") or 0)
        ay = int(anchor.get("sourceY") if anchor.get("sourceY") is not None else anchor.get("y") or 0)
        candidates = [
            (abs(cx - ax) + abs(cy - ay), object_id, cx, cy)
            for object_id, cx, cy in available_cities
            if object_id not in used_cities
        ]
        candidates.sort()
        _dist, city_id, cx, cy = candidates[0]
        used_cities.add(city_id)
        city_spawn = {
            "type": 0,
            "id": city_id,
            "owner": olden_owner,
            "spawnType": OLDEN_SPAWN_TYPE_AI,
            "spawnPointType": OLDEN_SPAWN_POINT_TYPE_CITY,
            "isLocked": True,
        }
        upsert_property_row_by_id(spawn_rows, city_spawn)
        bindings.append(
            {
                "oldenOwner": olden_owner,
                "heroObjectIds": hero_ids,
                "cityObjectId": city_id,
                "citySourceXY": [cx, cy],
                "manhattanDistance": _dist,
            }
        )
    properties["propSpawns"] = spawn_rows
    return {
        "boundCount": len(bindings),
        "bindings": bindings,
        "policy": "nearest_unbound_neutral_town_to_orphan_ai_hero",
        "proofBoundary": (
            "generated_artifact; AI capital assignment for neutral-town AB/SoD maps is "
            "lossy vs H3 capture-on-visit and remains runtime-unvalidated"
        ),
    }

def plan_ai_multi_faction_city_owner_split(
    properties: dict[str, Any],
    *,
    human_olden_owner: int,
    protect_city_ids: set[int] | frozenset[int] | None = None,
) -> dict[int, int]:
    """Plan city remaps so each Olden owner has exactly one city factionSid.

    StoryHub StartSide.fractionSid paints every bound city as that Bot's faction, so
    mixed-faction H3 AI players (common in RoE Liberation) must become multiple Olden
    Bot sides. Majority faction (city count, then factionSid) keeps the original owner;
    minority-faction cities move to the lowest free Olden owner in 1..8. Heroes are not
    remapped here — city binding is the Session.Loader failure mode.

    ``protect_city_ids`` (campaign-granted human start town) must stay on their current
    owner: that city's faction becomes the keep faction so the grant town is never
    remapped away from the human.
    """
    protected = {int(x) for x in (protect_city_ids or ())}
    spawn_rows = properties.get("propSpawns")
    if not isinstance(spawn_rows, list):
        raise ValueError("propSpawns must be a list before AI multi-faction city split")
    city_faction_by_id = {
        int(row["id"]): str(row.get("factionSid") or "")
        for row in (properties.get("propCities") or [])
        if isinstance(row, dict) and isinstance(row.get("id"), int)
    }
    cities_by_owner: dict[int, list[tuple[int, str]]] = {}
    used_owners: set[int] = set()
    for row in spawn_rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            continue
        owner = row.get("owner")
        if not isinstance(owner, int):
            continue
        used_owners.add(owner)
        if row.get("spawnPointType") != OLDEN_SPAWN_POINT_TYPE_CITY:
            continue
        # Include the human owner: StoryHub paints every bound city as the Player
        # fractionSid. Mixed-faction human towns (Steadwick Tower + Castle starts)
        # must split the minority onto a Bot owner or Loader/city init can NRE.
        faction = city_faction_by_id.get(int(row["id"]), "")
        if not faction:
            raise ValueError(
                f"city propSpawn {int(row['id'])} owner {owner} missing propCities.factionSid"
            )
        cities_by_owner.setdefault(owner, []).append((int(row["id"]), faction))

    remaps: dict[int, int] = {}
    free_owners = [
        owner
        for owner in range(1, 9)
        if owner not in used_owners and owner != human_olden_owner
    ]
    free_index = 0
    for owner, cities in sorted(cities_by_owner.items()):
        factions = sorted({faction for _, faction in cities})
        if len(factions) <= 1:
            continue
        counts: Counter[str] = Counter(faction for _, faction in cities)
        protected_here = [(cid, fac) for cid, fac in cities if cid in protected]
        if protected_here:
            protected_factions = sorted({fac for _, fac in protected_here})
            if len(protected_factions) != 1:
                raise ValueError(
                    f"owner {owner} protect_city_ids span multiple factions "
                    f"{protected_factions}; cannot keep campaign-granted start"
                )
            keep_faction = protected_factions[0]
        else:
            # Deterministic majority: highest count, then lexicographic factionSid.
            keep_faction = sorted(counts.keys(), key=lambda sid: (-counts[sid], sid))[0]
        minority = [sid for sid in factions if sid != keep_faction]
        for faction in minority:
            if free_index >= len(free_owners):
                raise ValueError(
                    f"AI multi-faction city split exhausted Olden owners 1..8; "
                    f"owner {owner} still needs faction {faction!r} "
                    f"(keep={keep_faction!r} counts={dict(counts)})"
                )
            new_owner = free_owners[free_index]
            free_index += 1
            used_owners.add(new_owner)
            for object_id, city_faction in cities:
                if city_faction == faction:
                    if object_id in protected:
                        raise ValueError(
                            f"refuse remapping protected campaign-granted city {object_id} "
                            f"from owner {owner} to {new_owner}"
                        )
                    remaps[object_id] = new_owner
    return remaps

def apply_explicit_ai_owner_faction_split(
    properties: dict[str, Any],
    *,
    remaps: dict[int, int],
) -> dict[str, Any]:
    """Remap propSpawns.owner for explicit object ids (fail-closed if any id missing)."""
    if not remaps:
        return {
            "policy": "explicit_object_id_olden_owner_remap",
            "remappedObjectIds": [],
            "owners": {},
        }
    spawn_rows = properties.get("propSpawns")
    if not isinstance(spawn_rows, list):
        raise ValueError("propSpawns must be a list before AI owner faction split")
    by_id = {
        int(row["id"]): row
        for row in spawn_rows
        if isinstance(row, dict) and isinstance(row.get("id"), int)
    }
    missing = sorted(object_id for object_id in remaps if object_id not in by_id)
    if missing:
        raise ValueError(
            f"AI owner faction split remaps missing propSpawns rows for object ids {missing}"
        )
    owners_before_after: dict[str, dict[str, int]] = {}
    for object_id, new_owner in sorted(remaps.items()):
        if not isinstance(new_owner, int) or new_owner < 1 or new_owner > 8:
            raise ValueError(f"split olden owner must be 1..8, got {new_owner!r} for id {object_id}")
        row = by_id[object_id]
        before = row.get("owner")
        if not isinstance(before, int):
            raise ValueError(f"propSpawns {object_id} owner must be int before split; got {before!r}")
        row["owner"] = new_owner
        # Minority cities peeled off a mixed-faction human (or any) side become Bot towns.
        # Leaving spawnType=HUMAN creates a second Player propSpawn and breaks StoryHub.
        row["spawnType"] = OLDEN_SPAWN_TYPE_AI
        owners_before_after[str(object_id)] = {"before": before, "after": new_owner}
    return {
        "policy": "explicit_object_id_olden_owner_remap",
        "remappedObjectIds": sorted(remaps),
        "owners": owners_before_after,
        "proofBoundary": (
            "source/static remap only; StoryHub Bot sides must declare matching spawns owners; "
            "owner 4 Bot fractionSid follows Terraneus city (homm3_dungeon); Xyron spawns via "
            "propHeroes campaign parity hook; runtime Xyron visibility remains user-unvalidated"
        ),
    }

def renumber_map_owners_to_native_compact(
    properties: dict[str, Any],
    *,
    human_olden_owner: int,
) -> dict[str, Any]:
    """Renumber map owner indices to the native compact scheme (human=1, AI=2..N).

    Every stock Olden map and every runtime-proven authored scenario (brine_bell,
    emberwatch) numbers players 1..playersCount with the human as owner 1. The H3M
    translation previously carried the HoMM3 player color (+1) as the Olden owner,
    so a non-red human (e.g. A Gryphon's Heart color 5 -> owner 6) referenced a
    player seat the runtime never creates and the town grant silently failed.

    Rewrites ``owner`` on propSpawns / propOwners / propCities rows. Fail-closed:
    any owner referenced by propOwners/propCities that has no propSpawns seat is an
    orphan side and refuses emit. Call this after every owner-mutating pass
    (faction split, orphan binds) and before meta.spawns / install manifest build.
    """
    spawn_owners = sorted(
        {
            int(row["owner"])
            for row in (properties.get("propSpawns") or [])
            if isinstance(row, dict) and isinstance(row.get("owner"), int)
        }
    )
    if human_olden_owner not in spawn_owners:
        raise ValueError(
            f"human olden owner {human_olden_owner} missing from propSpawns owners "
            f"{spawn_owners}; cannot renumber"
        )
    ai_owners = [owner for owner in spawn_owners if owner != human_olden_owner]
    mapping = {human_olden_owner: 1}
    for index, owner in enumerate(ai_owners, start=2):
        mapping[owner] = index
    rewritten: dict[str, int] = {}
    for prop_key in ("propSpawns", "propOwners", "propCities"):
        count = 0
        for row in properties.get(prop_key) or []:
            if not isinstance(row, dict) or not isinstance(row.get("owner"), int):
                continue
            owner = int(row["owner"])
            if owner not in mapping:
                raise ValueError(
                    f"{prop_key} row id={row.get('id')} owner {owner} has no propSpawns "
                    f"seat (owners {spawn_owners}); orphan side refused"
                )
            row["owner"] = mapping[owner]
            count += 1
        rewritten[prop_key] = count
    return {
        "policy": "native_compact_owner_renumber_human_first",
        "ownerMapping": {str(k): v for k, v in sorted(mapping.items())},
        "humanOldenOwner": 1,
        "playersCount": len(mapping),
        "rewrittenRowCounts": rewritten,
    }

