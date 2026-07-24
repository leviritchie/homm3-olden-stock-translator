#!/usr/bin/env python3
"""Merge vanilla_stock quest LocKit tokens into Core.zip customMaps.json packs.

Quest name/desc fields on stock maps are Loc SIDs resolved from
``Lang/*/texts/customMaps.json``. Inline English shows as ``LOC:<text>`` in-game.

This installer upserts translator-emitted tokens into every language pack that
already ships ``customMaps.json`` (English text is used for all packs so UI
never falls back to missing-SID placeholders).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

CUSTOM_MAPS_MEMBER_RE = re.compile(r"^Lang/[^/]+/texts/customMaps\.json$")


def _load_tokens_doc(raw: bytes) -> dict[str, Any]:
    doc = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(doc, dict) or not isinstance(doc.get("tokens"), list):
        raise SystemExit("customMaps.json must be an object with a tokens array")
    return doc


def _dump_tokens_doc(doc: dict[str, Any]) -> bytes:
    # Stock packs use UTF-8 BOM; keep that shape.
    payload = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    return ("\ufeff" + payload).encode("utf-8")


def _upsert_tokens(doc: dict[str, Any], tokens: list[dict[str, str]]) -> int:
    by_sid = {
        str(row.get("sid") or ""): row
        for row in doc["tokens"]
        if isinstance(row, dict) and row.get("sid")
    }
    changed = 0
    for token in tokens:
        sid = str(token.get("sid") or "").strip()
        text = str(token.get("text") or "")
        if not sid:
            raise SystemExit(f"loc token missing sid: {token!r}")
        existing = by_sid.get(sid)
        if existing is None:
            row = {"sid": sid, "text": text}
            doc["tokens"].append(row)
            by_sid[sid] = row
            changed += 1
        elif str(existing.get("text") or "") != text:
            existing["text"] = text
            changed += 1
    return changed


def install_custom_maps_loc(
    *,
    core_zip: Path,
    tokens: list[dict[str, str]],
) -> dict[str, Any]:
    if not core_zip.is_file():
        raise SystemExit(f"Core.zip not found: {core_zip}")
    if not tokens:
        raise SystemExit("no Loc tokens to install")

    backup = core_zip.with_suffix(core_zip.suffix + ".bak_vanilla_stock_custom_maps_loc")
    if not backup.is_file():
        shutil.copy2(core_zip, backup)

    tmp = core_zip.with_suffix(core_zip.suffix + ".tmp_vanilla_stock_custom_maps_loc")
    if tmp.exists():
        tmp.unlink()

    members_touched: list[str] = []
    total_upserts = 0
    with zipfile.ZipFile(core_zip, "r") as src, zipfile.ZipFile(
        tmp, "w", compression=zipfile.ZIP_DEFLATED
    ) as dst:
        for info in src.infolist():
            name = info.filename.replace("\\", "/")
            if CUSTOM_MAPS_MEMBER_RE.match(name):
                doc = _load_tokens_doc(src.read(info.filename))
                changed = _upsert_tokens(doc, tokens)
                total_upserts += changed
                members_touched.append(name)
                dst.writestr(name, _dump_tokens_doc(doc))
            else:
                dst.writestr(info, src.read(info.filename))

    if not members_touched:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"Core.zip has no Lang/*/texts/customMaps.json members: {core_zip}")

    tmp.replace(core_zip)

    # Verify required SIDs exist in English pack after write.
    required = sorted({str(t["sid"]) for t in tokens})
    with zipfile.ZipFile(core_zip, "r") as verify:
        english = "Lang/english/texts/customMaps.json"
        if english not in {n.replace("\\", "/") for n in verify.namelist()}:
            raise SystemExit(f"missing {english} after Loc install")
        doc = _load_tokens_doc(verify.read(english))
        present = {
            str(row.get("sid") or "")
            for row in doc["tokens"]
            if isinstance(row, dict)
        }
    missing = [sid for sid in required if sid not in present]
    if missing:
        raise SystemExit(f"Loc SIDs missing from english customMaps after install: {missing}")

    return {
        "coreZip": str(core_zip),
        "backup": str(backup),
        "membersTouched": members_touched,
        "memberCount": len(members_touched),
        "upsertOperations": total_upserts,
        "tokenCount": len(required),
        "tokenSids": required,
        "result": "PASS",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-zip", type=Path, required=True)
    parser.add_argument(
        "--tokens-json",
        type=Path,
        required=True,
        help="JSON list of {sid,text} rows, or object with locTokens/tokens array",
    )
    args = parser.parse_args(argv)
    payload = json.loads(args.tokens_json.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        tokens = payload.get("locTokens") or payload.get("tokens") or []
    else:
        tokens = payload
    if not isinstance(tokens, list):
        raise SystemExit("tokens JSON must be a list or object with locTokens/tokens")
    report = install_custom_maps_loc(core_zip=args.core_zip, tokens=tokens)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
