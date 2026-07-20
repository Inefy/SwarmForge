"""
Training analysis for the Batten bot family.

Reads training_log.csv (match results), data/games_<bot>.jsonl (per-game
decisions + outcomes) and data/strategies_<bot>.json (learned memory), and
prints standings, matchups, learning trends, and per-dimension winrates.

Usage:  py -3 analyze.py
"""

import csv
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
BOTS = ["Battencruiser", "Zacling", "Protodd"]


def match_log():
    path = os.path.join(HERE, "training_log.csv")
    if not os.path.exists(path):
        print("No training_log.csv yet - run train.py first.")
        return []
    return list(csv.DictReader(open(path)))


def print_standings(rows):
    wins = defaultdict(int)
    games = defaultdict(int)
    matchup = defaultdict(lambda: [0, 0])
    crashes = 0
    for r in rows:
        if r["result1"] in ("Crash", "Timeout"):
            crashes += 1
            continue
        a, b = r["bot1"], r["bot2"]
        games[a] += 1
        games[b] += 1
        key = tuple(sorted([a, b]))
        if r["result1"] == "Victory":
            wins[a] += 1
            matchup[key][0 if a == key[0] else 1] += 1
        elif r["result2"] == "Victory":
            wins[b] += 1
            matchup[key][0 if b == key[0] else 1] += 1
    print("=" * 60)
    print("MATCH LOG: %d games (%d crashes/timeouts)" % (len(rows), crashes))
    for b in sorted(games, key=lambda x: -(wins[x] / max(1, games[x]))):
        print("  %-14s %3d-%-3d (%.0f%%)" % (b, wins[b], games[b] - wins[b], 100.0 * wins[b] / max(1, games[b])))
    print("\nMatchups:")
    for (a, b), (w1, w2) in sorted(matchup.items()):
        print("  %-14s %2d - %-2d %s" % (a, w1, w2, b))
    # Trend: halves.
    half = len(rows) // 2
    for label, chunk in (("first half", rows[:half]), ("last half", rows[half:])):
        w = defaultdict(int)
        g = defaultdict(int)
        for r in chunk:
            if r["result1"] in ("Crash", "Timeout"):
                continue
            g[r["bot1"]] += 1
            g[r["bot2"]] += 1
            if r["result1"] == "Victory":
                w[r["bot1"]] += 1
            elif r["result2"] == "Victory":
                w[r["bot2"]] += 1
        trend = "  ".join("%s %.0f%%" % (b, 100.0 * w[b] / max(1, g[b])) for b in BOTS if g[b])
        print("Trend %-10s %s" % (label + ":", trend))


def print_dimensions():
    for bot in BOTS:
        path = os.path.join(HERE, bot, "data", "games_%s.jsonl" % bot.lower())
        if not os.path.exists(path):
            path = os.path.join(HERE, "data", "games_%s.jsonl" % bot.lower())
        if not os.path.exists(path):
            continue
        entries = []
        for line in open(path):
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
        if not entries:
            continue
        print("\n" + "=" * 60)
        print("%s - %d logged games" % (bot.upper(), len(entries)))
        skip = {"ts", "opponent", "enemy_race", "won", "stats", "observed"}
        dims_present = sorted({k for e in entries for k in e if k not in skip})
        for dim in dims_present:
            counter = defaultdict(lambda: [0, 0])
            for e in entries:
                if dim in e:
                    counter[e[dim]][0 if e.get("won") else 1] += 1
            parts = []
            for arm, (w, l) in sorted(counter.items(), key=lambda kv: -(kv[1][0] / max(1, sum(kv[1])))):
                parts.append("%s %d-%d (%.0f%%)" % (arm, w, l, 100.0 * w / max(1, w + l)))
            if parts:
                print("  %-11s %s" % (dim + ":", " | ".join(parts)))
        # Average game length won vs lost.
        won_t = [e["stats"]["end_time"] for e in entries if e.get("won") and e.get("stats", {}).get("end_time")]
        lost_t = [e["stats"]["end_time"] for e in entries if not e.get("won") and e.get("stats", {}).get("end_time")]
        if won_t or lost_t:
            print("  game time:  wins avg %ss | losses avg %ss" % (
                int(sum(won_t) / len(won_t)) if won_t else "-",
                int(sum(lost_t) / len(lost_t)) if lost_t else "-",
            ))


def print_memory():
    print("\n" + "=" * 60)
    print("LEARNED MEMORY (current best arms per opponent)")
    for bot in BOTS:
        path = os.path.join(HERE, bot, "data", "strategies_%s.json" % bot.lower())
        if not os.path.exists(path):
            path = os.path.join(HERE, "data", "strategies_%s.json" % bot.lower())
        if not os.path.exists(path):
            continue
        data = json.load(open(path))
        print("\n--- %s" % bot)
        for opp, rec in data.items():
            dims = rec.get("dims", {})
            if not dims:
                continue
            best = {}
            for dim, arms in dims.items():
                scored = sorted(
                    arms.items(),
                    key=lambda kv: -((kv[1][0] + 1.0) / (kv[1][0] + kv[1][1] + 2.0)),
                )
                if scored:
                    arm, wl = scored[0]
                    best[dim] = "%s (%d-%d)" % (arm, wl[0], wl[1])
            print("  vs %-22s %s" % (opp, ", ".join("%s=%s" % kv for kv in sorted(best.items()))))


if __name__ == "__main__":
    rows = match_log()
    if rows:
        print_standings(rows)
    print_dimensions()
    print_memory()
