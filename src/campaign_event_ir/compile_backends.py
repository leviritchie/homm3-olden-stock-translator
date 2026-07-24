"""Minimal compile helpers for stock timed-resource arms."""
from __future__ import annotations

def timed_resource_arm_counter_sid(mission_id: str, source_index: int | None) -> str:
    return f"{mission_id}_timed_grant_{source_index}_armed"
