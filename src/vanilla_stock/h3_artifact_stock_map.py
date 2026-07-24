"""H3 artifact id → stock Core item SID (exact / near-exact name matches only).

Unmapped ids stay omitted with named gaps. Do not invent role-similar stand-ins.
"""

from __future__ import annotations

# Proven by stock Lang/english/texts/artifacts.json display names + map object rows.
STOCK_H3_ARTIFACT_ID_TO_ITEM_SID: dict[int, str] = {
    # Endless Bag of Gold → stock "Endless Bag"
    116: "endless_bag_artifact",
    # Ogre's Club of Havoc (exact name match)
    10: "ogres_club_of_havoc_artifact",
}


def stock_item_sid_for_h3_artifact_id(artifact_id: int) -> str | None:
    return STOCK_H3_ARTIFACT_ID_TO_ITEM_SID.get(int(artifact_id))
