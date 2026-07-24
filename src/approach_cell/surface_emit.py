"""Stock-safe footprint / rotation helpers carved from the private surface emit.

Only the symbols needed by ``vanilla_stock`` and ``gate_face_rotation`` live here.
No Golden Era Core overlays, OfflineUnlockMod paths, or campaign localization.
"""

from __future__ import annotations

from typing import Any

# Mutable atlas size — callers temporarily assign for the active map envelope.
OLDEN_WIDTH = 72
OLDEN_HEIGHT = 72

OLDEN_DEFAULT_ROTATION = 0
OLDEN_MIRRORED_ROTATION = 10

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
