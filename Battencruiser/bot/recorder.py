"""
In-game flight recorder for the Batten bot family.

Snapshots key macro metrics on a fixed cadence and writes one compact trace per
game to data/traces_<bot>.jsonl. analyze_replays.py reads those traces and
diagnoses *why* games were lost (supply block, mineral float, passive army,
run over early, lost even fights, out-teched). Fully defensive: every method is
wrapped so it can never raise into the game loop or affect play.
"""

import json
import os
import time

SNAP_INTERVAL = 8.0     # game-seconds between snapshots
MAX_SNAPS = 260         # ~35 game-minutes, bounds file size

WORKER_NAMES = {"SCV", "PROBE", "DRONE", "MULE"}
AIR_NAMES = {
    "MUTALISK", "CORRUPTOR", "BROODLORD", "VIPER", "BANSHEE", "BATTLECRUISER",
    "RAVEN", "LIBERATOR", "LIBERATORAG", "VIKINGFIGHTER", "MEDIVAC",
    "VOIDRAY", "CARRIER", "TEMPEST", "PHOENIX", "ORACLE", "MOTHERSHIP",
}
CLOAK_NAMES = {"DARKTEMPLAR", "BANSHEE", "GHOST", "LURKERMPBURROWED", "MOTHERSHIP"}


def _step_ms(bot):
    try:
        return round(float(bot.step_time[1]), 1)
    except Exception:
        return 0.0


class GameRecorder:
    def __init__(self, bot_name, lower):
        self.bot_name = bot_name
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._data_dir = os.path.join(base, "data")
        self.path = os.path.join(self._data_dir, "traces_%s.jsonl" % lower)
        self.snaps = []
        self._next_t = 0.0

    def maybe_snapshot(self, bot):
        try:
            t = bot.time
            if t < self._next_t or len(self.snaps) >= MAX_SNAPS:
                return
            self._next_t = t + SNAP_INTERVAL
            earmy = 0.0
            ecounts = {}
            for u in bot.enemy_units:
                try:
                    if u.is_structure:
                        continue
                    name = u.type_id.name
                    if name not in WORKER_NAMES:
                        earmy += 2.0
                    ecounts[name] = ecounts.get(name, 0) + 1
                except Exception:
                    continue
            top = sorted(ecounts.items(), key=lambda kv: -kv[1])[:3]
            self.snaps.append({
                "t": round(t, 1),
                "sup": int(bot.supply_used),
                "cap": int(bot.supply_cap),
                "army": int(bot.supply_army),
                "wk": int(bot.supply_workers),
                "min": int(bot.minerals),
                "gas": int(bot.vespene),
                "bases": bot.townhalls.amount,
                "earmy": round(earmy, 1),
                "ms": _step_ms(bot),
                "atk": bool(getattr(bot, "attack_mode", False)),
                "blk": bool(bot.supply_left <= 0 and bot.supply_cap < 200),
                "etop": top,
            })
        except Exception:
            pass

    def finish(self, bot, won, picks):
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            entry = {
                "ts": int(time.time()),
                "bot": self.bot_name,
                "opponent": str(getattr(bot, "opponent_id", "")),
                "won": bool(won),
                "picks": picks or {},
                "snaps": self.snaps,
            }
            with open(self.path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass
