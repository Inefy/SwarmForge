"""Build the generated bot directory consumed by the local ladder runner."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LADDER = ROOT / "ladder"
MANIFEST = LADDER / "manifest.json"
GENERATED = LADDER / "bots"


def _safe_extract(archive: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        for member in members:
            target = (destination / member.filename).resolve()
            if target != destination_resolved and destination_resolved not in target.parents:
                raise ValueError(f"unsafe path in {archive.name}: {member.filename}")
        source.extractall(destination)


def _copy_workspace(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination / "source",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "data", ".git"),
    )
    wrapper = destination / "run.py"
    wrapper.write_text(
        """import os\nimport runpy\nimport sys\nfrom pathlib import Path\n\nHERE = Path(__file__).resolve().parent\nSOURCE = HERE / \"source\"\nsys.path.insert(0, str(SOURCE))\nos.environ.setdefault(\"SWARMFORGE_TRAINING\", \"1\")\nif Path(\"/training-data\").is_dir():\n    os.chdir(\"/training-data\")\nrunpy.run_path(str(SOURCE / \"run.py\"), run_name=\"__main__\")\n""",
        encoding="utf-8",
    )


def _make_executable(path: Path) -> None:
    if os.name != "nt" and path.is_file():
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def prepare(dry_run: bool = False) -> list[dict]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    bots = manifest["bots"]
    if not dry_run:
        if GENERATED.exists():
            resolved = GENERATED.resolve()
            if resolved != (LADDER / "bots").resolve():
                raise RuntimeError(f"refusing to remove unexpected path: {resolved}")
            shutil.rmtree(GENERATED)
        GENERATED.mkdir(parents=True)

    for bot in bots:
        destination = GENERATED / bot["id"]
        source = (LADDER / bot["source"]).resolve()
        if not source.exists():
            raise FileNotFoundError(f"missing source for {bot['id']}: {source}")
        if dry_run:
            print(f"would stage {bot['id']} from {source}")
            continue
        destination.mkdir(parents=True)
        if bot["kind"] == "archive":
            _safe_extract(source, destination)
        elif bot["kind"] == "workspace":
            _copy_workspace(source, destination)
        else:
            raise ValueError(f"unknown bot kind: {bot['kind']}")
        for path in destination.rglob("*"):
            _make_executable(path)

    return bots


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="validate sources without extracting/copying")
    args = parser.parse_args()
    bots = prepare(args.dry_run)
    print(f"validated {len(bots)} bots" if args.dry_run else f"staged {len(bots)} bots in {GENERATED}")


if __name__ == "__main__":
    main()
