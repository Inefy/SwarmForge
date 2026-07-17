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

try:
    import psutil
except ImportError:  # Training still works; cleanup is best-effort without it.
    psutil = None

HERE = os.path.dirname(os.path.abspath(__file__))
BOTS = ["Battencruiser", "Zacling", "Protodd"]
PAIRINGS = [("Battencruiser", "Zacling"), ("Battencruiser", "Protodd"), ("Zacling", "Protodd")]

MAP_CANDIDATES = [
    "IncorporealAIE_v4", "LeyLinesAIE_v3", "PersephoneAIE_v4",
    "PylonAIE_v4", "TorchesAIE_v4",
    "AbyssalReefAIE", "AcropolisAIE", "AutomatonAIE", "EphemeronAIE",
    "InterloperAIE", "ThunderbirdAIE", "PylonAIE", "TorchesAIE", "PersephoneAIE",
    "AutomatonLE", "AcropolisLE", "AbyssalReefLE",
]


def _sc2_pids():
    if psutil is None:
        return set()
    found = set()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if (proc.info.get("name") or "").lower() == "sc2_x64.exe":
                found.add(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return found


def _terminate_process_tree(pid):
    """Kill one match and its SC2 children without touching unrelated processes."""
    if psutil is not None:
        try:
            parent = psutil.Process(pid)
            processes = parent.children(recursive=True) + [parent]
            for proc in processes:
                try:
                    proc.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            _, alive = psutil.wait_procs(processes, timeout=5)
            for proc in alive:
                try:
                    proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def _cleanup_match_sc2(before):
    if psutil is None:
        return
    for pid in _sc2_pids() - before:
        try:
            psutil.Process(pid).kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def run_match_process(cmd, timeout):
    """Run a match with bounded output and guaranteed child-process cleanup."""
    before_sc2 = _sc2_pids()
    env = os.environ.copy()
    env["SWARMFORGE_TRAINING"] = "1"
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(proc.pid)
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = exc.output or "", exc.stderr or ""
        _cleanup_match_sc2(before_sc2)
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
    _cleanup_match_sc2(before_sc2)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def append_crash_log(game_number, bot_a, bot_b, game_map, stdout="", stderr="", error=""):
    """Keep one compact diagnostic record without interrupting an unattended run."""
    try:
        with open(os.path.join(HERE, "crashes.log"), "a") as cl:
            cl.write("\n==== game %d: %s vs %s on %s ====\n" % (game_number, bot_a, bot_b, game_map))
            if error:
                cl.write("ERROR: %s\n" % error)
            cl.write("STDOUT tail:\n%s\n" % ((str(stdout) if stdout else "<empty>")[-3000:]))
            cl.write("STDERR tail:\n%s\n" % ((str(stderr) if stderr else "<empty>")[-3000:]))
    except Exception:
        pass


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
    parser.add_argument("--ai-share", type=float, default=0.2,
                        help="Fraction of games vs the non-cheating Very Hard AI (0 to disable)")
    parser.add_argument("--match-timeout", type=int, default=3600,
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
            ai_race = None
            if args.ai_share > 0 and random.random() < args.ai_share:
                bot_a = random.choice(BOTS)
                ai_race = random.choice(["Terran", "Zerg", "Protoss"])
                bot_b = "AI_" + ai_race
            game_map = random.choice(maps_pool)
            game_number += 1
            start = time.time()
            print("Game %d: %s vs %s on %s ..." % (game_number, bot_a, bot_b, game_map), end=" ", flush=True)
            try:
                cmd = [sys.executable, os.path.join(HERE, "play_match.py"),
                       "--bot1", bot_a, "--map", game_map,
                       "--game-time-limit", str(args.game_time_limit)]
                if ai_race:
                    cmd += ["--computer-race", ai_race]
                else:
                    cmd += ["--bot2", bot_b]
                proc = run_match_process(cmd, args.match_timeout)
                out = proc.stdout or ""
                runtime_errors = [
                    ln for ln in out.splitlines()
                    if ln.startswith("[") and " failed: " in ln
                ]
                if runtime_errors:
                    print("%d bot runtime warning(s); see crashes.log ... " % len(runtime_errors), end="")
                    append_crash_log(
                        game_number, bot_a, bot_b, game_map, "\n".join(runtime_errors), proc.stderr,
                        "bot recovered from runtime errors",
                    )
                line = next((ln for ln in out.splitlines() if ln.startswith("RESULT_JSON ")), None)
                if line is None:
                    crashes += 1
                    print("no result (crash?)")
                    append_crash_log(
                        game_number, bot_a, bot_b, game_map, out, proc.stderr,
                        "match exited without RESULT_JSON (code %s)" % proc.returncode,
                    )
                    writer.writerow([int(time.time()), game_number, bot_a, bot_b, game_map,
                                     "Crash", "Crash", int(time.time() - start)])
                    log.flush()
                    continue
                res = json.loads(line[len("RESULT_JSON "):])
                r1, r2 = res["result1"], res["result2"]
                if r1 == "Victory":
                    wins[bot_a] += 1
                    if bot_b in losses:
                        losses[bot_b] += 1
                elif r2 == "Victory":
                    if bot_b in wins:
                        wins[bot_b] += 1
                    losses[bot_a] += 1
                duration = int(time.time() - start)
                writer.writerow([int(time.time()), game_number, bot_a, bot_b, game_map, r1, r2, duration])
                log.flush()
                standings = "  |  ".join(
                    "%s %d-%d" % (b, wins[b], losses[b]) for b in BOTS
                )
                print("%s / %s (%ds)  [%s]" % (r1, r2, duration, standings))
            except subprocess.TimeoutExpired as exc:
                crashes += 1
                print("timed out, killed")
                append_crash_log(
                    game_number, bot_a, bot_b, game_map,
                    getattr(exc, "output", ""), getattr(exc, "stderr", ""),
                    "match exceeded %d real-time seconds" % args.match_timeout,
                )
                writer.writerow([int(time.time()), game_number, bot_a, bot_b, game_map,
                                 "Timeout", "Timeout", args.match_timeout])
                log.flush()
            except Exception as exc:
                crashes += 1
                print("trainer error: %s: %s" % (type(exc).__name__, exc))
                append_crash_log(
                    game_number, bot_a, bot_b, game_map,
                    error="%s: %s" % (type(exc).__name__, exc),
                )
                writer.writerow([int(time.time()), game_number, bot_a, bot_b, game_map,
                                 "Crash", "Crash", int(time.time() - start)])
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
