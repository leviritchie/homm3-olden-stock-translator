#!/usr/bin/env python3
"""HoMM3 traversal parity via scenery catalog match + functional keep-donor + gap-fill.

Runtime-proven constraints:
- Resizing ObjectConfig (or 1x1 squad shrinks) breaks visuals/interaction.
- Rewriting functional `nodes` (GATE rings → sparse south-center GATE) breaks
  squads (`?`), structure approachability, and mirror-10 facing. Native
  ObjectConfig GATE/mirror layouts must be preserved.

Policy:
- Scenery: catalog SID with identical occupied footprint when one exists; else
  solid-square sizing. Residual H3 blocked cells are gap-filled.
- Functional (incl. squads, towns, mines, …): keep the donor SID and native
  nodes/prefs; GATE-align placement to the HoMM3 visit cell using the donor's
  native GATE; gap-fill uncovered H3 non-visit blocks.

MiniLM-free. Does not import surface_emit / approach_cell (callers pass helpers).
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from typing import Any, Callable

import functional_emit_reservation as fer

SCHEMA = "homm3.olden_exact_footprint_overlay.v1"
CLONE_SID_PREFIX = "homm3_xfp_"
OVERLAY_OBJECT_MEMBER = "DB/map/objects/homecoming_exact_footprint_objects.json"
OVERLAY_MANIFEST_NAME = "homecoming_exact_footprint_overlay_manifest.json"
META_KEY = "homm3ExactFootprint"

# Never same-size clone these (special emit / infrastructure).
SKIP_CATEGORIES = frozenset({
    # hero_or_prison is NOT skipped: hero-spawner must keep_donor GATE-align to H3 visit.
    "campaign_briefing_script",
    "pathability_alignment_blocker",
    "emit_reservation_infrastructure",
    "block_parity_seal",
    "footprint_alignment_pad",
    "footprint_alignment_global_pad",
    "footprint_alignment_cluster",
    "boat_or_water_travel_object",
})

SKIP_SIDS = frozenset({
    "homm3_pathing_blocker",
    "homm3_envelope_padding_blocker",
    "homm3_boat",
    # Built-in spawn: native GATE-ring nodes are required for test/random_squad.
    # Placement uses GATE-on-visit emit in surface_emit (runtime stand follows emit).
    "random-squad",
})

# Land-on overlay SIDs are fine donors; logic aliases map to native logic rows.
LOGIC_DONOR_ALIASES: dict[str, str] = {
    "homm3_landon_castle_city": "homm3_castle_city",
    "homm3_landon_dungeon_city": "homm3_dungeon_city",
    "homm3_landon_barracks_castle_1": "barracks_homm3_castle_1",
    "homm3_landon_barracks_castle_2": "barracks_homm3_castle_2",
    "homm3_landon_barracks_castle_3": "barracks_homm3_castle_3",
    "homm3_landon_barracks_castle_4": "barracks_homm3_castle_4",
    "homm3_landon_barracks_castle_5": "barracks_homm3_castle_5",
    "homm3_landon_barracks_castle_6": "barracks_homm3_castle_6",
    "homm3_landon_barracks_castle_7": "barracks_homm3_castle_7",
    "homm3_landon_map_event_marker": "fx_quest_mark_gold_01",
}


@dataclass(frozen=True)
class SameSizeFootprint:
    size_x: int
    size_z: int
    nodes: tuple[int, ...]
    gate_config_x: int | None
    gate_config_z: int | None
    in_grid_block_count: int
    overflow_block_count: int
    kind: str

    @property
    def shape_key(self) -> str:
        return f"{self.size_x}x{self.size_z}:{','.join(str(n) for n in self.nodes)}"


def prefer_gate_local(size_x: int, size_z: int) -> tuple[int, int]:
    """South-edge center GATE — matches land-on approachability for 3x3."""
    if size_x <= 0 or size_z <= 0:
        raise ValueError(f"invalid donor size for GATE local: {size_x}x{size_z}")
    return size_x // 2, size_z - 1


def classify_object_config_shape(config: dict[str, Any], sid: str | None = None) -> str:
    """Classify ObjectConfig into catalogue shape vocabulary (§2).

    Returns one of: landon | single_gate | gate_ring | solid | other.

    GATE cells (nodes==2) are interaction *markers* only — they do not occupy or
    claim map cells. Placement:
    - landon / single_gate → GATE-align (put the interact marker on H3 visit)
    - gate_ring → GATE-on-visit emit (occupied or open center; runtime stand follows emit)
    """
    resolved = sid if isinstance(sid, str) and sid else str(config.get("id") or "")
    if resolved.startswith("homm3_landon_"):
        return "landon"
    # Scorched-earth subterranean portal: OCC + multiple GATE markers. Still
    # GATE-align the preferred south-east marker to the H3 visit (not gate_ring
    # center-pivot, which would put visit on OCC).
    if resolved == "homm3_subterranean_gate_portal":
        return "single_gate"
    size = donor_size(config)
    nodes = config.get("nodes")
    if size is None or not isinstance(nodes, list) or len(nodes) != size[0] * size[1]:
        return "other"
    gate_count = sum(1 for value in nodes if int(value) == 2)
    occupied_count = sum(1 for value in nodes if int(value) == 1)
    # Resource/item/chest: 8 GATE markers + 1 center occupied. Squads: 8 markers + open center.
    if gate_count >= 4 and occupied_count <= 1:
        return "gate_ring"
    if gate_count == 1:
        return "single_gate"
    if gate_count == 0 and occupied_count > 0:
        return "solid"
    return "other"


def footprint_from_donor_config(config: dict[str, Any]) -> SameSizeFootprint:
    """Native donor size/nodes with a preferred GATE for visit-aligned placement.

    GATE preference applies to landon / single_gate only (buildings/portals whose
    interact marker should sit on the H3 visit). GATE-ring donors keep gateConfig
    unset so callers pivot the center (occupied body or open squad center) onto
    the H3 visit — markers orbit and do not claim cells.
    """
    size = donor_size(config)
    if size is None:
        raise ValueError(f"donor config missing usable size for footprint: {config.get('id')!r}")
    size_x, size_z = size
    raw_nodes = config.get("nodes")
    if isinstance(raw_nodes, list) and len(raw_nodes) == size_x * size_z:
        nodes = tuple(int(n) for n in raw_nodes)
    else:
        nodes = tuple(0 for _ in range(size_x * size_z))
    resolved_sid = str(config.get("id") or "")
    shape = classify_object_config_shape(config, resolved_sid)
    gates = [(i % size_x, i // size_x) for i, value in enumerate(nodes) if value == 2]
    gate_x = gate_z = None
    if shape in {"single_gate", "landon"} and gates:
        preferred = prefer_gate_local(size_x, size_z)
        if preferred in gates:
            gate_x, gate_z = preferred
        elif len(gates) == 1:
            gate_x, gate_z = gates[0]
        else:
            gate_x, gate_z = max(gates, key=lambda cell: (cell[1], -abs(cell[0] - (size_x // 2))))
    return SameSizeFootprint(
        size_x=size_x,
        size_z=size_z,
        nodes=nodes,
        gate_config_x=gate_x,
        gate_config_z=gate_z,
        in_grid_block_count=sum(1 for value in nodes if value == 1),
        overflow_block_count=0,
        kind="functional",
    )


def same_size_nodes_from_h3(
    *,
    size_x: int,
    size_z: int,
    block_offsets: set[tuple[int, int]],
    visit_offsets: set[tuple[int, int]],
    kind: str,
) -> SameSizeFootprint:
    """Project H3 BR-relative masks onto a fixed donor size grid.

    Functional with a unique visit: GATE sits at south-center; H3 cells are
    projected relative to that visit. Scenery / no-visit: BR maps to the
    south-east corner of the donor grid (land-on BR convention).
    """
    if kind not in {"scenery", "functional"}:
        raise ValueError(f"unsupported same-size footprint kind: {kind!r}")
    if size_x <= 0 or size_z <= 0:
        raise ValueError(f"invalid donor size: {size_x}x{size_z}")

    visits = set(visit_offsets) if kind == "functional" else set()
    blocks = set(block_offsets)
    nodes = [0] * (size_x * size_z)
    overflow = 0
    in_grid_blocks = 0

    if kind == "functional" and len(visits) == 1:
        visit_dx, visit_dy = next(iter(visits))
        gate_x, gate_z = prefer_gate_local(size_x, size_z)
        nodes[gate_z * size_x + gate_x] = 2
        for dx, dy in blocks:
            if (dx, dy) in visits:
                continue
            cx = gate_x + (dx - visit_dx)
            cz = gate_z + (dy - visit_dy)
            if 0 <= cx < size_x and 0 <= cz < size_z:
                idx = cz * size_x + cx
                if nodes[idx] != 2:
                    nodes[idx] = 1
                    in_grid_blocks += 1
            else:
                overflow += 1
        return SameSizeFootprint(
            size_x=size_x,
            size_z=size_z,
            nodes=tuple(nodes),
            gate_config_x=gate_x,
            gate_config_z=gate_z,
            in_grid_block_count=in_grid_blocks,
            overflow_block_count=overflow,
            kind=kind,
        )

    # Scenery or functional without a unique visit: BR at SE of donor grid.
    br_x, br_z = size_x - 1, size_z - 1
    for dx, dy in blocks:
        cx = br_x + dx
        cz = br_z + dy
        if 0 <= cx < size_x and 0 <= cz < size_z:
            nodes[cz * size_x + cx] = 1
            in_grid_blocks += 1
        else:
            overflow += 1
    # Multi-visit functional without unique visit: mark all in-grid visits as GATE.
    gate_x = gate_z = None
    if kind == "functional" and visits:
        for dx, dy in visits:
            cx = br_x + dx
            cz = br_z + dy
            if 0 <= cx < size_x and 0 <= cz < size_z:
                nodes[cz * size_x + cx] = 2
                gate_x, gate_z = cx, cz
    return SameSizeFootprint(
        size_x=size_x,
        size_z=size_z,
        nodes=tuple(nodes),
        gate_config_x=gate_x,
        gate_config_z=gate_z,
        in_grid_block_count=in_grid_blocks,
        overflow_block_count=overflow,
        kind=kind,
    )


def clone_sid_for(donor_sid: str, footprint: SameSizeFootprint) -> str:
    digest = hashlib.sha1(
        f"samesize|{footprint.kind}|{donor_sid}|{footprint.shape_key}".encode("utf-8")
    ).hexdigest()[:12]
    return f"{CLONE_SID_PREFIX}{footprint.kind[0]}_{digest}"


def apply_same_size_clone(
    donor_row: dict[str, Any],
    *,
    donor_sid: str,
    clone_sid: str,
    footprint: SameSizeFootprint,
) -> dict[str, Any]:
    if donor_row.get("id") != donor_sid:
        raise ValueError(
            f"same-size clone donor id mismatch for {clone_sid}: "
            f"expected {donor_sid!r}, found {donor_row.get('id')!r}"
        )
    size_x = donor_row.get("sizeX")
    size_z = donor_row.get("sizeZ")
    if size_x != footprint.size_x or size_z != footprint.size_z:
        raise ValueError(
            f"same-size clone must preserve donor size for {clone_sid}: "
            f"donor={size_x}x{size_z} footprint={footprint.size_x}x{footprint.size_z}"
        )
    row = json.loads(json.dumps(donor_row, ensure_ascii=False))
    row["id"] = clone_sid
    row["nodes"] = list(footprint.nodes)
    row["canBeMirrored"] = False
    # Do not invent gateInsideObject. Preserve donor flag; GATE markers are not
    # occupancy and Resource-style rings must not become "visit inside object".
    row[META_KEY] = {
        "schema": "homm3.exact_footprint_meta.v1",
        "mode": "same_size_nodes_plus_overflow_blockers",
        "kind": footprint.kind,
        "donorSid": donor_sid,
        "gateConfig": (
            [footprint.gate_config_x, footprint.gate_config_z]
            if footprint.gate_config_x is not None and footprint.gate_config_z is not None
            else None
        ),
        "inGridBlockCount": footprint.in_grid_block_count,
        "overflowBlockCount": footprint.overflow_block_count,
        "shapeKey": footprint.shape_key,
    }
    return row


def is_exact_footprint_sid(sid: str) -> bool:
    return isinstance(sid, str) and sid.startswith(CLONE_SID_PREFIX)


def footprint_spec_from_config(config: dict[str, Any]) -> SameSizeFootprint | None:
    meta = config.get(META_KEY)
    if not isinstance(meta, dict):
        return None
    mode = meta.get("mode")
    if mode not in {
        None,
        "same_size_nodes_plus_overflow_blockers",
    }:
        return None
    nodes = config.get("nodes")
    size_x = config.get("sizeX")
    size_z = config.get("sizeZ")
    if not isinstance(size_x, int) or not isinstance(size_z, int) or not isinstance(nodes, list):
        return None
    gate = meta.get("gateConfig")
    gate_x = gate_z = None
    if isinstance(gate, list) and len(gate) == 2:
        gate_x, gate_z = int(gate[0]), int(gate[1])
    elif 2 in nodes:
        idx = nodes.index(2)
        gate_x, gate_z = idx % size_x, idx // size_x
    return SameSizeFootprint(
        size_x=size_x,
        size_z=size_z,
        nodes=tuple(int(n) for n in nodes),
        gate_config_x=gate_x,
        gate_config_z=gate_z,
        in_grid_block_count=int(meta.get("inGridBlockCount") or sum(1 for n in nodes if n == 1)),
        overflow_block_count=int(meta.get("overflowBlockCount") or 0),
        kind=str(meta.get("kind") or "functional"),
    )


def is_scenery_entity(entity: dict[str, Any], donor_sid: str) -> bool:
    return (
        entity.get("category") == "payloadless_object_unclassified_for_current_scope"
        and donor_sid in fer.SUPPRESSIBLE_SOURCE_SCENERY_SIDS
    )


def should_skip_entity(entity: dict[str, Any], donor_sid: str) -> bool:
    if fer.is_infrastructure_sid(donor_sid) or donor_sid in SKIP_SIDS:
        return True
    if is_exact_footprint_sid(donor_sid):
        return True
    category = str(entity.get("category") or "")
    if category in SKIP_CATEGORIES:
        return True
    return False


def donor_size(config: dict[str, Any]) -> tuple[int, int] | None:
    size_x = config.get("sizeX")
    size_z = config.get("sizeZ")
    if isinstance(size_x, int) and isinstance(size_z, int) and size_x > 0 and size_z > 0:
        nodes = config.get("nodes")
        if nodes is not None and (not isinstance(nodes, list) or len(nodes) != size_x * size_z):
            return None
        return size_x, size_z
    return None


def occupied_br_offsets_from_config(config: dict[str, Any]) -> frozenset[tuple[int, int]]:
    size = donor_size(config)
    nodes = config.get("nodes")
    if size is None or not isinstance(nodes, list):
        return frozenset()
    size_x, size_z = size
    br_cx, br_cz = size_x - 1, size_z - 1
    out: set[tuple[int, int]] = set()
    for cz in range(size_z):
        for cx in range(size_x):
            if nodes[cz * size_x + cx] != 1:
                continue
            out.add((cx - br_cx, cz - br_cz))
    return frozenset(out)


def scenery_family_prefix(sid: str | None) -> str | None:
    """Coarse family used to keep catalog remaps inside the same blocking scenery line."""
    if not isinstance(sid, str) or not sid:
        return None
    lower = sid.lower()
    for prefix in (
        "mountain_snow",
        "mountain_dead",
        "mountain_dirt",
        "mountain_desert",
        "mountain_lava",
        "mountain_water",
        "mountain_green",
        "pinetree_snow",
        "tree_dead",
        "tree_dirt",
        "tree_lava",
        "tree_autumn",
        "pinetree",
        "pool_snow",
        "pool_dead",
        "pool_dirt",
        "pool_desert",
        "pool_lava",
        "pool",
    ):
        if lower.startswith(prefix):
            return prefix
    return lower.split("_", 1)[0] if "_" in lower else lower


def find_catalog_scenery_match(
    *,
    block_offsets: set[tuple[int, int]],
    configs: dict[str, dict[str, Any]],
    preferred_donor: str,
) -> str | None:
    target = frozenset(block_offsets)
    preferred_family = scenery_family_prefix(preferred_donor)
    candidates = sorted(fer.SUPPRESSIBLE_SOURCE_SCENERY_SIDS & set(configs))
    if preferred_donor in candidates:
        candidates.remove(preferred_donor)
        candidates.insert(0, preferred_donor)
    # Same-family only: do not remap mountain_snow_* onto mountain_green_* just because
    # the occupied mask matches — that erases DEF families into wrong biomes / holes.
    matches = [
        sid
        for sid in candidates
        if occupied_br_offsets_from_config(configs[sid]) == target
        and (preferred_family is None or scenery_family_prefix(sid) == preferred_family)
    ]
    if not matches:
        return None
    if preferred_donor in matches:
        return preferred_donor
    return matches[0]


@dataclass
class ExactFootprintEntry:
    source_key: str
    source_index: int
    donor_sid: str
    result_sid: str
    kind: str
    action: str  # catalog_match | same_size_clone | keep_donor
    footprint: SameSizeFootprint | None


@dataclass
class ExactFootprintRegistry:
    by_source_index: dict[int, ExactFootprintEntry]
    clone_rows: dict[str, dict[str, Any]]
    logic_clones: list[dict[str, Any]]
    stats: dict[str, Any]


def build_exact_footprint_registry(
    entities: list[dict[str, Any]],
    *,
    resolve_donor: Callable[[dict[str, Any]], str | None],
    block_offsets_for: Callable[[dict[str, Any]], set[tuple[int, int]]],
    visit_offsets_for: Callable[[dict[str, Any]], set[tuple[int, int]]],
    configs: dict[str, dict[str, Any]],
    core_zip_path: str | Any,
    in_source_bounds: Callable[[dict[str, Any]], bool],
) -> ExactFootprintRegistry:
    by_source_index: dict[int, ExactFootprintEntry] = {}
    clone_rows: dict[str, dict[str, Any]] = {}
    logic_clones: list[dict[str, Any]] = []
    stats = {
        "sceneryCatalogMatch": 0,
        "sceneryNoCatalogSolidSquare": 0,
        "sceneryClone": 0,
        "functionalSameSizeClone": 0,
        "functionalKeepDonor": 0,
        "functionalSkippedNoSize": 0,
        "skipped": 0,
        "uniqueCloneSids": 0,
    }

    for entity in entities:
        if not in_source_bounds(entity):
            continue
        source_index = entity.get("sourceIndex")
        if not isinstance(source_index, int):
            raise ValueError(f"entity missing sourceIndex for exact footprint: {entity.get('sourceKey')}")
        donor_sid = resolve_donor(entity)
        if donor_sid is None:
            continue
        if should_skip_entity(entity, donor_sid):
            stats["skipped"] += 1
            continue
        if donor_sid not in configs:
            raise ValueError(
                f"exact footprint missing donor config for {entity.get('sourceKey')}: {donor_sid}"
            )
        scenery = is_scenery_entity(entity, donor_sid)

        if scenery:
            blocks = set(block_offsets_for(entity))
            catalog = find_catalog_scenery_match(
                block_offsets=blocks,
                configs=configs,
                preferred_donor=donor_sid,
            )
            if catalog is not None:
                # Multi-tile mountain meshes overhang ObjectConfig OCC onto H3-open
                # neighbors — refuse catalog and fall through to 1x1 sizing.
                catalog_fp = footprint_from_donor_config(configs[catalog])
                catalog_occ = (
                    sum(1 for value in catalog_fp.nodes if int(value) == 1)
                    if catalog_fp is not None
                    else 0
                )
                if catalog_occ > 1 and str(catalog).startswith("mountain_"):
                    catalog = None
            if catalog is not None:
                by_source_index[source_index] = ExactFootprintEntry(
                    source_key=str(entity.get("sourceKey") or ""),
                    source_index=source_index,
                    donor_sid=donor_sid,
                    result_sid=catalog,
                    kind="scenery",
                    action="catalog_match",
                    footprint=None,
                )
                stats["sceneryCatalogMatch"] += 1
                continue
            stats["sceneryNoCatalogSolidSquare"] += 1
            continue

        size = donor_size(configs[donor_sid])
        if size is None:
            stats["functionalSkippedNoSize"] += 1
            continue

        # Never rewrite functional ObjectConfig nodes. Keep native Resource/item/chest
        # donors as-is. Placement plants node on a GATE at the H3 visit (native map
        # pattern). propResParams use value=0. Do not clone to GATE-only 1x1.
        footprint = footprint_from_donor_config(configs[donor_sid])
        by_source_index[source_index] = ExactFootprintEntry(
            source_key=str(entity.get("sourceKey") or ""),
            source_index=source_index,
            donor_sid=donor_sid,
            result_sid=donor_sid,
            kind="functional",
            action="keep_donor",
            footprint=footprint,
        )
        stats["functionalKeepDonor"] += 1

    stats["uniqueCloneSids"] = len(clone_rows)
    stats["uniqueLogicClones"] = len(logic_clones)
    # Compat aliases for older validator/manifest readers.
    stats["functionalClone"] = stats["functionalSameSizeClone"]
    stats["sceneryClone"] = 0
    return ExactFootprintRegistry(
        by_source_index=by_source_index,
        clone_rows=clone_rows,
        logic_clones=logic_clones,
        stats=stats,
    )


def _load_logic_index(core_zip_path: str | Any) -> dict[str, tuple[str, dict[str, Any]]]:
    index: dict[str, tuple[str, dict[str, Any]]] = {}
    with zipfile.ZipFile(core_zip_path) as core:
        for name in core.namelist():
            if not (name.startswith("DB/objects_logic/") and name.endswith(".json")):
                continue
            doc = json.loads(core.read(name).decode("utf-8-sig"))
            rows = doc.get("array") if isinstance(doc, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    index.setdefault(row["id"], (name, row))
    return index


# Resource / camp logic is keyed for native SIDs (resource_gold, camp_fire_1, …).
# Cloning those rows under homm3_xfp_* ids without matching ObjectConfig makes
# Session.Loader resolve Meta map objects that do not exist → ArgumentNullException
# and an infinite Loader retry (observed on Steadwick's Liberation).
_FORBIDDEN_LOGIC_CLONE_MEMBERS = frozenset(
    {
        # Meta catalogs: Loader resolves these logic ids as map-object SIDs.
        "DB/objects_logic/res/resources.json",
        "DB/objects_logic/res/resources_camps.json",
        "DB/objects_logic/chests/chests.json",
        "DB/objects_logic/chests/camp_fire.json",
        "DB/objects_logic/chests/scroll_box.json",
        "DB/objects_logic/chests/enchanted_scroll_box.json",
        "DB/objects_logic/chests/mythic_scroll_box.json",
        "DB/objects_logic/chests/pandora_box.json",
    }
)


def _maybe_queue_logic_clone(
    *,
    donor_sid: str,
    clone_sid: str,
    logic_index: dict[str, tuple[str, dict[str, Any]]],
    logic_clones: list[dict[str, Any]],
    logic_clone_sids: set[str],
) -> None:
    if clone_sid in logic_clone_sids:
        return
    logic_sid = LOGIC_DONOR_ALIASES.get(donor_sid, donor_sid)
    hit = logic_index.get(logic_sid)
    if hit is None:
        return
    member, donor_row = hit
    if member in _FORBIDDEN_LOGIC_CLONE_MEMBERS:
        raise ValueError(
            f"exact footprint must not clone resource/camp logic for {clone_sid!r} "
            f"(donor={logic_sid!r} member={member}); orphan logic ids hang Session.Loader "
            f"via Meta map object lookup. Keep native resource_* / camp_fire donors."
        )
    clone_row = json.loads(json.dumps(donor_row, ensure_ascii=False))
    clone_row["id"] = clone_sid
    logic_clones.append({
        "member": member,
        "donorSid": logic_sid,
        "cloneSid": clone_sid,
        "row": clone_row,
    })
    logic_clone_sids.add(clone_sid)


def inject_registry_into_configs(
    configs: dict[str, dict[str, Any]],
    valid_ids: set[str],
    registry: ExactFootprintRegistry,
) -> None:
    for sid, row in registry.clone_rows.items():
        configs[sid] = row
        valid_ids.add(sid)


def ensure_scenery_catalog_configs(
    configs: dict[str, dict[str, Any]],
    *,
    core_zip_path: str | Any,
) -> None:
    missing = sorted(fer.SUPPRESSIBLE_SOURCE_SCENERY_SIDS - set(configs))
    if not missing:
        return
    want = set(missing)
    with zipfile.ZipFile(core_zip_path) as core:
        for name in core.namelist():
            if not want:
                break
            if not (name.startswith("DB/map/objects/") and name.endswith(".json")):
                continue
            doc = json.loads(core.read(name).decode("utf-8-sig"))
            rows = doc.get("array") if isinstance(doc, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict) and row.get("id") in want:
                    configs[row["id"]] = row
                    want.discard(row["id"])
    still_missing = sorted(want)
    if still_missing:
        raise ValueError(
            "exact footprint scenery catalog missing Core ObjectConfig rows: "
            + ", ".join(still_missing[:20])
        )


def remap_replacement_sid(
    entity: dict[str, Any],
    donor_sid: str,
    registry: ExactFootprintRegistry | None,
) -> tuple[str, str | None]:
    if registry is None:
        return donor_sid, None
    source_index = entity.get("sourceIndex")
    if not isinstance(source_index, int):
        return donor_sid, None
    entry = registry.by_source_index.get(source_index)
    if entry is None:
        return donor_sid, None
    if entry.donor_sid != donor_sid:
        raise ValueError(
            f"exact footprint donor drift for {entity.get('sourceKey')}: "
            f"resolve={donor_sid!r} registry_donor={entry.donor_sid!r}"
        )
    if entry.action == "catalog_match":
        return entry.result_sid, "exact_homm3_footprint_scenery_catalog"
    if entry.action == "same_size_clone":
        return entry.result_sid, "exact_homm3_traversal_same_size_clone"
    if entry.action == "clone":
        # Legacy action name — treat as same-size.
        return entry.result_sid, "exact_homm3_traversal_same_size_clone"
    return donor_sid, "exact_homm3_footprint_keep_donor"


def derive_gate_aligned_node(
    *,
    visit_node: int,
    footprint: SameSizeFootprint,
    grid_width: int,
    grid_height: int,
    default_node: int,
    rotation: int = 0,
    can_be_mirrored: bool = False,
) -> tuple[int, dict[str, Any]]:
    if footprint.gate_config_x is None or footprint.gate_config_z is None:
        raise ValueError("GATE-aligned placement requires gateConfig on same-size footprint")
    visit_x = visit_node % grid_width
    visit_y = visit_node // grid_width
    gate_x = footprint.gate_config_x
    gate_z = footprint.gate_config_z
    mirrored = rotation == 10 and can_be_mirrored
    placed_dx = footprint.size_x - 1 - gate_x if mirrored else gate_x
    anchor_x = visit_x - placed_dx
    anchor_y = visit_y - (footprint.size_z - 1 - gate_z)
    if not (0 <= anchor_x < grid_width and 0 <= anchor_y < grid_height):
        raise ValueError(
            f"same-size GATE-aligned anchor out of bounds: ({anchor_x},{anchor_y}) "
            f"visit={visit_node} gate=({gate_x},{gate_z}) size={footprint.size_x}x{footprint.size_z} "
            f"rotation={rotation} mirrored={mirrored}"
        )
    node = anchor_y * grid_width + anchor_x
    evidence = {
        "policy": "exact_homm3_traversal_keep_donor_gate_aligned",
        "reason": "native_donor_gate_equals_h3_visit",
        "decision": "align_olden_node_so_gate_equals_h3_visit",
        "nativeFootprint": {
            "width": footprint.size_x,
            "height": footprint.size_z,
            "cellCount": footprint.in_grid_block_count,
            "source": "exact_homm3_keep_donor_native_nodes",
            "gateLocal": [gate_x, gate_z],
            "overflowBlockCount": footprint.overflow_block_count,
            "kind": footprint.kind,
            "mirrored": mirrored,
        },
        "node": node,
        "visitNode": visit_node,
        "sourceAnchorNode": default_node,
    }
    return node, evidence
