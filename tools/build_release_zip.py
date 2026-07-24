#!/usr/bin/env python3
"""Build a GitHub Release zip users can download (source + launcher, no .exe)."""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = [
    "Convert-Map.bat",
    "Convert-Map.ps1",
    "LICENSE",
    "README.md",
    "pyproject.toml",
    "src",
    "tools",
    "examples",
    "user_maps/README.md",
]
SKIP_DIR_NAMES = {".git", ".runtime", "artifacts", "release_dist", "__pycache__", ".venv", "venv"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".map", ".h3m", ".h3c", ".lod"}


def should_skip(path: Path | str) -> bool:
    name = path if isinstance(path, str) else path.name
    suffix = "" if isinstance(path, str) else path.suffix.lower()
    if name in SKIP_DIR_NAMES:
        return True
    if suffix in SKIP_SUFFIXES:
        return True
    if name == "Core.zip":
        return True
    return False


def iter_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for item in INCLUDE:
        path = root / item
        if not path.exists():
            raise SystemExit(f"missing release member: {path}")
        if path.is_file():
            out.append(path)
            continue
        for child in path.rglob("*"):
            if not child.is_file():
                continue
            if any(should_skip(part) for part in child.relative_to(root).parts):
                continue
            if should_skip(child):
                continue
            out.append(child)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "release_dist")
    args = parser.parse_args(argv)

    name = f"homm3-olden-stock-translator-{args.version}"
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{name}.zip"
    if zip_path.exists():
        zip_path.unlink()

    files = iter_files(ROOT)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            arc = Path(name) / path.relative_to(ROOT)
            zf.write(path, arcname=arc.as_posix())

    print(f"wrote {zip_path} ({zip_path.stat().st_size} bytes, {len(files)} files)")
    print("Upload this zip as a GitHub Release asset. Do not attach Core.zip or .h3m files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
