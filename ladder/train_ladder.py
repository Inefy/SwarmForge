"""Schedule and run repeatable StarCraft II bot training batches."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import itertools
import json
import os
import shlex
import subprocess
import sys
import time
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LADDER = ROOT / "ladder"
MANIFEST = LADDER / "manifest.json"
MATCHES = LADDER / "matches"
RESULTS = LADDER / "results.json"
MAPS = [
    "IncorporealAIE_v4",
    "LeyLinesAIE_v3",
    "PersephoneAIE_v4",
    "PylonAIE_v4",
    "TorchesAIE_v4",
]


def load_bots() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["bots"]


def pairings(bots: list[dict], mode: str, include_self_play: bool) -> list[tuple[dict, dict]]:
    own = [bot for bot in bots if bot["kind"] == "workspace"]
    external = [bot for bot in bots if bot["kind"] != "workspace"]
    if mode == "all":
        selected = list(itertools.combinations(bots, 2))
    else:
        selected = list(itertools.product(own, external))
        if include_self_play:
            selected.extend(itertools.combinations(own, 2))
            selected.extend(itertools.combinations(external, 2))
    return [(a, b) for a, b in selected]


def write_matches(bots: list[dict], mode: str, include_self_play: bool, maps: list[str], round_number: int, limit: int | None) -> int:
    scheduled = pairings(bots, mode, include_self_play)
    rows = []
    for index, (first, second) in enumerate(scheduled):
        if round_number % 2:
            first, second = second, first
        rows.append([
            # local-play-bootstrap expects each bot as id, name, race, type.
            # The runner uses the bot id as the name as well, yielding nine
            # CSV fields total once the map is appended.
            first["id"], first["name"], first["race"], first["type"],
            second["id"], second["name"], second["race"], second["type"],
            maps[(index + round_number) % len(maps)],
        ])
    if limit is not None:
        rows = rows[:limit]
    with MATCHES.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    return len(rows)


def _wsl_path(path: Path, distro: str) -> str:
    # wslpath expects a Unix path when invoked from a WSL command. Convert
    # Windows drive paths ourselves instead of passing backslashes through the
    # WSL argument parser.
    windows_path = str(path)
    drive_match = re.match(r"^([A-Za-z]):[\\/]", windows_path)
    if os.name == "nt" and drive_match:
        drive = drive_match.group(1).lower()
        remainder = windows_path[3:].replace("\\", "/")
        return f"/mnt/{drive}/{remainder}"
    result = subprocess.run(
        ["wsl", "-d", distro, "--", "wslpath", "-a", str(path)],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def run_compose(distro: str, maps_dir: str) -> int:
    ladder_wsl = _wsl_path(LADDER, distro)
    command = (
        f"cd {shlex.quote(ladder_wsl)} && "
        f"export SC2_MAPS_DIR={shlex.quote(maps_dir)} && "
        "docker compose -f docker-compose.yml up "
        "--abort-on-container-exit --exit-code-from proxy_controller"
    )
    print(f"running Docker ladder in WSL distro {distro}: {ladder_wsl}")
    completed = subprocess.run(["wsl", "-d", distro, "--", "bash", "-lc", command])
    return completed.returncode


def append_result_history(round_number: int, count: int, exit_code: int) -> None:
    history = LADDER / "results-history.jsonl"
    record = {
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "round": round_number,
        "matches": count,
        "exit_code": exit_code,
    }
    with history.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, help="number of batches to run (default: 1 unless --hours is used)")
    parser.add_argument("--hours", type=float, help="keep running batches until this many hours elapse")
    parser.add_argument("--mode", choices=("focus", "all"), default="focus", help="focus: own vs imported; all: every pair")
    parser.add_argument("--all", dest="all_mode", action="store_true", help="alias for --mode all")
    parser.add_argument("--include-self-play", action="store_true", help="in focus mode, also schedule imported-vs-imported and own-vs-own")
    parser.add_argument("--games", type=int, help="cap the total number of games")
    parser.add_argument("--map", dest="map_name", action="append", help="map stem; repeat to provide a map pool")
    parser.add_argument("--distro", default="Ubuntu", help="WSL distro containing Docker")
    parser.add_argument("--sc2-maps", default="/mnt/e/games/StarCraft II/Maps", help="WSL path mounted into the SC2 container")
    parser.add_argument("--no-prepare", action="store_true", help="reuse the already staged ladder/bots directory")
    parser.add_argument("--dry-run", action="store_true", help="write no matches and only print the schedule")
    args = parser.parse_args()

    mode = "all" if args.all_mode else args.mode
    maps = args.map_name or MAPS
    if (args.rounds is not None and args.rounds < 1) or (args.hours is not None and args.hours <= 0):
        parser.error("--rounds must be positive and --hours must be greater than zero")
    if args.games is not None and args.games < 1:
        parser.error("--games must be positive")

    bots = load_bots()
    planned = pairings(bots, mode, args.include_self_play)
    print(f"{len(bots)} bots, {len(planned)} pairings per round, mode={mode}")
    if args.dry_run:
        for first, second in planned:
            print(f"  {first['id']} ({first['race']}) vs {second['id']} ({second['race']})")
        return 0

    if not args.no_prepare:
        subprocess.run([sys.executable, str(LADDER / "prepare_ladder.py")], check=True)
    LADDER.mkdir(exist_ok=True)
    (LADDER / "replays").mkdir(exist_ok=True)
    (LADDER / "logs").mkdir(exist_ok=True)
    # Results are per training session. Reset the generated file so a restart
    # cannot mix a prior stalled session into the new run's counters.
    RESULTS.write_text("{}", encoding="utf-8")

    started = time.monotonic()
    total_games = 0
    round_number = 0
    round_limit = args.rounds if args.rounds is not None else (10**9 if args.hours is not None else 1)
    while round_number < round_limit:
        if args.hours is not None and time.monotonic() - started >= args.hours * 3600:
            break
        remaining = None if args.games is None else args.games - total_games
        if remaining is not None and remaining <= 0:
            break
        round_number += 1
        count = write_matches(bots, mode, args.include_self_play, maps, round_number, remaining)
        if count == 0:
            break
        total_games += count
        exit_code = run_compose(args.distro, args.sc2_maps)
        append_result_history(round_number, count, exit_code)
        if exit_code != 0:
            return exit_code
        if args.games is not None and total_games >= args.games:
            break
    print(f"completed {total_games} scheduled games across {round_number} round(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
