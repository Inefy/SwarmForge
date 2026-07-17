"""
Open-ended per-opponent learning for Protodd, persisted via ./data.

Instead of one build bandit, FOUR independent learned dimensions combine into
~135 possible ways to play, explored axis by axis:
  * opening    - how the first minutes are spent
  * aggression - how big an army before attacking (0.55x .. 1.7x)
  * greed      - economy vs army: worker counts + expansion timing
  * tech       - which unit composition to lean into

Each arm is scored 65% by results vs THIS opponent, 35% by global results vs
everyone, plus a small exploration bonus so the bot never stops experimenting.
Opponent fingerprints (rushes, cloak, air, pressure timing) pre-adapt the next
game, and every game is appended to data/games_protodd.jsonl for analysis.
"""

import json
import os
import random
import tempfile
import time

OPENINGS = ['four_gate', 'stalker_immortal', 'proxy_gates']
BUILDS = OPENINGS  # alias used by run.py --build forcing
RACE_PRIORITY = {'Zerg': ['four_gate', 'stalker_immortal', 'proxy_gates'], 'Terran': ['stalker_immortal', 'four_gate', 'proxy_gates'], 'Protoss': ['four_gate', 'stalker_immortal', 'proxy_gates'], 'Random': ['four_gate', 'stalker_immortal', 'proxy_gates']}
RUSH_UNSAFE_OPENING = 'proxy_gates'

AGGRESSION_ARMS = [
    ("standard", 1.0), ("early", 0.75), ("very_early", 0.55),
    ("late", 1.3), ("very_late", 1.7),
]
# (name, worker cap multiplier, expansion clock shift in seconds)
GREED_ARMS = [("standard", 1.0, 0), ("greedy", 1.15, 50), ("lean", 0.85, -60)]
TECH_ARMS = ['blink_stalker', 'immortal_core', 'chargelot']

PROFILE_FLAGS = ["rushed", "worker_rush", "cannon_rush", "cloak", "air"]

DATA_DIR = "data"
DATA_FILE = os.path.join(DATA_DIR, "strategies_protodd.json")
GAME_LOG = os.path.join(DATA_DIR, "games_protodd.jsonl")
GLOBAL_KEY = "__global__"
EXPLORATION = 0.10


def _laplace(wl):
    wins, losses = wl[0], wl[1]
    return (wins + 1.0) / (wins + losses + 2.0)


class StrategyManager:
    def __init__(self, opponent_id, enemy_race_name="Random"):
        self.opponent_id = str(opponent_id) if opponent_id else "unknown"
        self.enemy_race_name = enemy_race_name
        self.priority = RACE_PRIORITY.get(enemy_race_name, RACE_PRIORITY["Random"])
        self.all_data = self._load()
        self.record = self._migrate(self.all_data.get(self.opponent_id, {}))
        self.global_record = self._migrate(self.all_data.get(GLOBAL_KEY, {}))
        self.profile = self.record.get("profile", {})
        self.observed = {}

        self.build = self._choose("opening", self._opening_candidates())
        self.opening = self.build
        aggr = self._choose("aggression", [a[0] for a in AGGRESSION_ARMS])
        self.aggression = aggr
        self.aggression_mult = dict((n, m) for n, m in AGGRESSION_ARMS)[aggr]
        greed = self._choose("greed", [g[0] for g in GREED_ARMS])
        self.greed = greed
        greed_info = dict((n, (m, s)) for n, m, s in GREED_ARMS)[greed]
        self.greed_worker_mult, self.greed_expand_shift = greed_info
        self.tech = self._choose("tech", list(TECH_ARMS))
        self.reported = False

    # ---------------------------------------------------------------- storage

    @staticmethod
    def _load():
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    @staticmethod
    def _migrate(record):
        """Upgrade v3 records (builds/aggr) to the v4 dims schema in place."""
        try:
            dims = record.setdefault("dims", {})
            if "builds" in record and "opening" not in dims:
                dims["opening"] = record["builds"]
            if "aggr" in record and "aggression" not in dims:
                dims["aggression"] = record["aggr"]
        except Exception:
            pass
        return record

    # --------------------------------------------------------------- profile

    def expects(self, flag, threshold=0.4):
        try:
            games = max(1, int(self.profile.get("games", 0)))
            return (float(self.profile.get(flag, 0)) / games) >= threshold and games >= 1
        except Exception:
            return False

    def expected_max_air(self):
        try:
            return int(self.profile.get("max_air", 0))
        except Exception:
            return 0

    def observe(self, key, value=True):
        try:
            if key == "max_air":
                self.observed[key] = max(int(value), int(self.observed.get(key, 0)))
            elif key == "first_pressure":
                current = self.observed.get(key)
                self.observed[key] = float(value) if current is None else min(current, float(value))
            else:
                self.observed[key] = bool(value)
        except Exception:
            pass

    # --------------------------------------------------------------- choosing

    def _opening_candidates(self):
        candidates = list(self.priority)
        if (
            RUSH_UNSAFE_OPENING
            and (self.expects("worker_rush") or self.expects("rushed"))
            and len(candidates) > 1
        ):
            candidates = [c for c in candidates if c != RUSH_UNSAFE_OPENING] or candidates
        return candidates

    def _choose(self, dim, candidates):
        opp = self.record.get("dims", {}).get(dim, {})
        glob = self.global_record.get("dims", {}).get(dim, {})

        def score(item):
            index, name = item
            s = 0.65 * _laplace(opp.get(name, [0, 0])) + 0.35 * _laplace(glob.get(name, [0, 0]))
            s += random.uniform(0.0, EXPLORATION)     # keep experimenting
            s += (len(candidates) - index) * 0.005    # tiny order bias for game 1
            return s

        _, best = max(enumerate(candidates), key=score)
        return best

    # -------------------------------------------------------------- reporting

    def report(self, won, stats=None):
        if self.reported:
            return
        self.reported = True
        try:
            picks = {
                "opening": self.opening, "aggression": self.aggression,
                "greed": self.greed, "tech": self.tech,
            }
            for record in (self.record, self.global_record):
                dims = record.setdefault("dims", {})
                for dim, arm in picks.items():
                    wl = dims.setdefault(dim, {}).setdefault(arm, [0, 0])
                    wl[0 if won else 1] += 1
                record["games"] = int(record.get("games", 0)) + 1

            profile = self.record.setdefault("profile", {})
            profile["games"] = int(profile.get("games", 0)) + 1
            for flag in PROFILE_FLAGS:
                if self.observed.get(flag):
                    profile[flag] = int(profile.get(flag, 0)) + 1
            if "max_air" in self.observed:
                profile["max_air"] = max(int(profile.get("max_air", 0)), int(self.observed["max_air"]))
            if "first_pressure" in self.observed:
                previous = profile.get("first_pressure")
                fp = float(self.observed["first_pressure"])
                profile["first_pressure"] = fp if previous is None else min(float(previous), fp)

            self.record["last_result"] = "win" if won else "loss"
            self.all_data[self.opponent_id] = self.record
            self.all_data[GLOBAL_KEY] = self.global_record
            self._save()
            self._log_game(won, picks, stats)
        except Exception:
            pass

    def _log_game(self, won, picks, stats):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            entry = {
                "ts": int(time.time()),
                "opponent": self.opponent_id,
                "enemy_race": self.enemy_race_name,
                "won": bool(won),
            }
            entry.update(picks)
            if stats:
                entry["stats"] = stats
            if self.observed:
                entry["observed"] = dict(self.observed)
            with open(GAME_LOG, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def _save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(self.all_data, f, indent=1)
            os.replace(tmp_path, DATA_FILE)
        except Exception:
            try:
                with open(DATA_FILE, "w") as f:
                    json.dump(self.all_data, f)
            except Exception:
                pass
