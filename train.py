"""
Self-play training arena for Battencruiser (T), Zacling (Z) and Protodd (P).

Runs bot-vs-bot matches round-robin for hours. Every game feeds each bot's
persistent learning (build bandit, attack-timing bandit, opponent profile) in
./data/strategies_<bot>.json. Each match runs in its own subprocess, so a
crashed game never stops the session. Ctrl+C to stop early - progress is
already saved after every game.

Usage:
    py -3 train.py --hours 8
    py -3 train.py --games 60 --map AutomatonLE

When the session ends (or you Ctrl+C), the learned data is copied into each
bot's own data/ folder, so `make_ladder_zip.py` ships the trained knowledge.
"""

import argparse
import csv
import itertools
import json
import os
import random
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BOTS = ["Battencruiser", "Zacling", "Protodd"]
PAIRINGS = [("Battencruiser", "Zacling"), ("Battencruiser", "Protodd"), ("Zacling", "Protodd")]

MAP_CANDIDATES = [
    "PersephoneAIE_v4", "PylonAIE_v4", "TorchesAIE_v4",
    "AbyssalReefAIE", "AcropolisAIE", "AutomatonAIE", "EphemeronAIE",
    "InterloperAIE", "ThunderbirdAIE", "PylonAIE", "TorchesAIE", "PersephoneAIE",
    "AutomatonLE", "AcropolisLE", "AbyssalReefLE",
]


def installed_maps(requested):
    if requested:
        return [requested]
    try:
        from sc2 import maps
    except ImportError:
        print("ERROR: burnysc2 not installed. Run: py -3 -m pip install --upgrade burnysc2")
        sys.exit(1)
    found = []
    for name in MAP_CANDIDATES:
        try:
            maps.get(name)
            found.append(name)
        except Exception:
            continue
    if not found:
        print("ERROR: no known maps installed. Download from https://aiarena.net/wiki/maps/")
        print(r"and unzip into C:\Program Files (x86)\StarCraft II\Maps")
        sys.exit(1)
    return found


def sync_learned_data():
    data_dir = os.path.join(HERE, "data")
    if not os.path.isdir(data_dir):
        return
    for bot in BOTS:
        src = os.path.join(data_dir, "strategies_%s.json" % bot.lower())
        if os.path.exists(src):
            dst_dir = os.path.join(HERE, bot, "data")
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy2(src, os.path.join(dst_dir, os.path.basename(src)))
            print("Synced learning ->", os.path.join(bot, "data", os.path.basename(src)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=8.0, help="How long to train (wall clock)")
    parser.add_argument("--games", type=int, default=0, help="Stop after N games instead")
    parser.add_argument("--map", type=str, default=None, help="Force one map")
    parser.add_argument("--game-time-limit", type=int, default=10800,
                        help="Maximum in-game seconds per match (default 3 game-hours)")
    parser.add_argument("--match-timeout", type=int, default=5400,
                        help="Real-time seconds before a match subprocess is killed")
    args = parser.parse_args()

    os.chdir(HERE)
    os.makedirs("data", exist_ok=True)
    maps_pool = installed_maps(args.map)
    print("Maps in rotation:", ", ".join(maps_pool))

    log_path = os.path.join(HERE, "training_log.csv")
    new_log = not os.path.exists(log_path)
    log = open(log_path, "a", newline="")
    writer = csv.writer(log)
    if new_log:
        writer.writerow(["timestamp", "game", "bot1", "bot2", "map", "result1", "result2", "seconds"])

    wins = {b: 0 for b in BOTS}
    losses = {b: 0 for b in BOTS}
    crashes = 0
    game_number = 0
    deadline = time.time() + args.hours * 3600
    pairing_cycle = itertools.cycle(PAIRINGS)

    print("Training until %s%s. Ctrl+C to stop (progress saves after every game).\n"
          % (time.strftime("%H:%M", time.localtime(deadline)),
             " or %d games" % args.games if args.games else ""))

    try:
        while time.time() < deadline and (not args.games or game_number < args.games):
            bot_a, bot_b = next(pairing_cycle)
            # Alternate who hosts (spawn side) for fairness.
            if game_number % 2 == 1:
                bot_a, bot_b = bot_b, bot_a
            game_map = random.choice(maps_pool)
            game_number += 1
            start = time.time()
            print("Game %d: %s vs %s on %s ..." % (game_number, bot_a, bot_b, game_map), end=" ", flush=True)
            try:
                proc = subprocess.run(
                    [sys.executable, os.path.join(HERE, "play_match.py"),
                     "--bot1", bot_a, "--bot2", bot_b, "--map", game_map,
                     "--game-time-limit", str(args.game_time_limit)],
                    capture_output=True, text=True, timeout=args.match_timeout,
                )
                out = proc.stdout or ""
                line = next((ln for ln in out.splitlines() if ln.startswith("RESULT_JSON ")), None)
                if line is None:
                    crashes += 1
                    print("no result (crash?)")
                    writer.writerow([int(time.time()), game_number, bot_a, bot_b, game_map,
                                     "Crash", "Crash", int(time.time() - start)])
                    log.flush()
                    continue
                res = json.loads(line[len("RESULT_JSON "):])
                r1, r2 = res["result1"], res["result2"]
                if r1 == "Victory":
                    wins[bot_a] += 1
                    losses[bot_b] += 1
                elif r2 == "Victory":
                    wins[bot_b] += 1
                    losses[bot_a] += 1
                duration = int(time.time() - start)
                writer.writerow([int(time.time()), game_number, bot_a, bot_b, game_map, r1, r2, duration])
                log.flush()
                standings = "  |  ".join(
                    "%s %d-%d" % (b, wins[b], losses[b]) for b in BOTS
                )
                print("%s / %s (%ds)  [%s]" % (r1, r2, duration, standings))
            except subprocess.TimeoutExpired:
                crashes += 1
                print("timed out, killed")
                writer.writerow([int(time.time()), game_number, bot_a, bot_b, game_map,
                                 "Timeout", "Timeout", args.match_timeout])
                log.flush()
    except KeyboardInterrupt:
        print("\nStopped by user.")

    log.close()
    print("\n===== SESSION COMPLETE: %d games, %d crashes/timeouts =====" % (game_number, crashes))
    for b in BOTS:
        total = wins[b] + losses[b]
        rate = (100.0 * wins[b] / total) if total else 0.0
        print("  %-13s %3d-%-3d  (%.0f%%)" % (b, wins[b], losses[b], rate))
    sync_learned_data()
    print("\nLearned strategy data synced into each bot folder.")
    print("Run make_ladder_zip.py inside each bot folder to ship the trained bots.")


if __name__ == "__main__":
    main()
