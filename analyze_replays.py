"""
Loss diagnosis for the Batten bot family.

Reads the flight-recorder traces (data/traces_<bot>.jsonl) produced during
training and works out *why* games were lost, not just that they were. For
every loss it runs a set of detectors over the macro time-series and reports
the ranked failure modes per bot (and per opponent), each with a concrete fix.

Usage:  py -3 analyze_replays.py
        py -3 analyze_replays.py --bot Zacling --opponent arena_Battencruiser
"""

import argparse
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
BOTS = ["Battencruiser", "Zacling", "Protodd"]

AIR_NAMES = {
    "MUTALISK", "CORRUPTOR", "BROODLORD", "VIPER", "BANSHEE", "BATTLECRUISER",
    "RAVEN", "LIBERATOR", "LIBERATORAG", "VIKINGFIGHTER", "VOIDRAY",
    "CARRIER", "TEMPEST", "PHOENIX", "ORACLE", "MOTHERSHIP",
}
CLOAK_NAMES = {"DARKTEMPLAR", "BANSHEE", "GHOST", "LURKERMPBURROWED", "MOTHERSHIP"}

FIXES = {
    "supply_block": "Supply-blocked too long - raise depot/overlord/pylon threshold or add production.",
    "mineral_float": "Floating minerals - add production buildings or expand sooner; money isn't becoming army.",
    "under_saturated": "Under-saturated economy - build more workers / take bases earlier.",
    "over_greedy": "Too many workers for the army - shift the greed dimension leaner or attack sooner.",
    "died_early_underdef": "Run over early - profile says this opponent hits fast; add static defense and hold the ramp.",
    "lost_even_fight": "Had army parity but still lost the fight - micro/composition problem (see the micro layer).",
    "passive_army": "Army sat idle with no pressure - lower the attack threshold or push before maxing.",
    "out_teched_air": "Lost to air you couldn't answer - scout for air tech and build anti-air earlier.",
    "out_teched_cloak": "Lost to cloak without detection - get detection/scans up as a standing habit.",
    "laggy": "Slow steps (>60ms avg) - risk of aiarena timeouts; reduce per-frame work or raise game_step sooner.",
}


def load_traces(bot):
    path = os.path.join(HERE, "data", "traces_%s.jsonl" % bot.lower())
    out = []
    if not os.path.exists(path):
        return out
    for line in open(path):
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def diagnose(trace):
    """Return the set of failure-mode tags that fired for one lost game."""
    snaps = trace.get("snaps") or []
    if len(snaps) < 3:
        return set()
    tags = set()
    total_t = snaps[-1]["t"] or 1
    late = [s for s in snaps if s["t"] > total_t * 0.4] or snaps[-3:]
    end = snaps[-1]

    blocked_frac = sum(1 for s in snaps if s.get("blk")) / len(snaps)
    if blocked_frac > 0.12:
        tags.add("supply_block")

    float_min = sum(s["min"] for s in late) / len(late)
    if float_min > 500 and end["army"] < 60:
        tags.add("mineral_float")

    bases = max(1, end["bases"])
    if total_t > 360 and end["wk"] < bases * 12:
        tags.add("under_saturated")
    if end["wk"] > bases * 18 and end["army"] < end["earmy"]:
        tags.add("over_greedy")

    if total_t < 300 and end["army"] < 15:
        tags.add("died_early_underdef")

    # Had army >= enemy's estimate but still lost -> lost the fight itself.
    if end["army"] >= max(10, end["earmy"] * 1.1) and end["army"] > 10:
        tags.add("lost_even_fight")

    passive = [s for s in late if not s.get("atk") and s["army"] >= 12]
    if late and len(passive) / len(late) > 0.5 and end["army"] >= 12:
        tags.add("passive_army")

    # Tech we couldn't answer, from the enemy composition near death.
    ms_vals = [s.get("ms", 0) for s in snaps if s.get("ms")]
    if ms_vals and sorted(ms_vals)[len(ms_vals) // 2] > 60:
        tags.add("laggy")

    late_types = set()
    for s in late:
        for name, _n in s.get("etop", []):
            late_types.add(name)
    if late_types & AIR_NAMES:
        tags.add("out_teched_air")
    if late_types & CLOAK_NAMES:
        tags.add("out_teched_cloak")

    return tags


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", default=None, choices=BOTS)
    parser.add_argument("--opponent", default=None)
    args = parser.parse_args()

    bots = [args.bot] if args.bot else BOTS
    for bot in bots:
        traces = load_traces(bot)
        if args.opponent:
            traces = [t for t in traces if t.get("opponent") == args.opponent]
        losses = [t for t in traces if not t.get("won")]
        wins = [t for t in traces if t.get("won")]
        print("=" * 62)
        print("%s - %d traces (%d wins, %d losses)" % (bot, len(traces), len(wins), len(losses)))
        if not losses:
            print("  no losses recorded yet")
            continue

        tag_counts = defaultdict(int)
        per_opp = defaultdict(lambda: defaultdict(int))
        per_opp_losses = defaultdict(int)
        for t in losses:
            tags = diagnose(t)
            per_opp_losses[t.get("opponent", "?")] += 1
            for tag in tags:
                tag_counts[tag] += 1
                per_opp[t.get("opponent", "?")][tag] += 1

        print("  Top failure modes across %d losses:" % len(losses))
        for tag, n in sorted(tag_counts.items(), key=lambda kv: -kv[1]):
            print("   %3d%%  %-20s %s" % (100 * n // len(losses), tag, FIXES.get(tag, "")))

        # Average length of wins vs losses for macro context.
        def avg_end(ts, key):
            vals = [t["snaps"][-1][key] for t in ts if t.get("snaps")]
            return sum(vals) / len(vals) if vals else 0
        print("  End-state: wins army %.0f / workers %.0f vs losses army %.0f / workers %.0f" % (
            avg_end(wins, "army"), avg_end(wins, "wk"), avg_end(losses, "army"), avg_end(losses, "wk")))

        if not args.opponent and len(per_opp) > 1:
            print("  Worst matchup breakdown:")
            for opp in sorted(per_opp_losses, key=lambda o: -per_opp_losses[o]):
                top = sorted(per_opp[opp].items(), key=lambda kv: -kv[1])[:2]
                summary = ", ".join("%s x%d" % (tag, n) for tag, n in top) or "mixed"
                print("   vs %-24s %2d losses - mostly: %s" % (opp, per_opp_losses[opp], summary))


if __name__ == "__main__":
    main()
