"""
Per-opponent learning for Protodd, persisted between games via ./data.

Three systems:
 1. Build bandit   - win/loss per build per opponent (Laplace-smoothed winrate).
 2. Timing bandit  - attack early / on time / late, learned per opponent.
 3. Opponent profile - fingerprints rushes, worker rushes, cannon rushes, cloak,
    air comps and earliest pressure so the NEXT game starts pre-adapted.
"""

import json
import os
import tempfile

BUILDS = ['four_gate', 'stalker_immortal', 'proxy_gates']

RACE_PRIORITY = {'Zerg': ['four_gate', 'stalker_immortal', 'proxy_gates'], 'Terran': ['stalker_immortal', 'four_gate', 'proxy_gates'], 'Protoss': ['four_gate', 'stalker_immortal', 'proxy_gates'], 'Random': ['four_gate', 'stalker_immortal', 'proxy_gates']}

RUSH_UNSAFE_BUILD = 'proxy_gates'

AGGRESSION_ARMS = [("standard", 1.0), ("early", 0.7), ("late", 1.4)]
PROFILE_FLAGS = ["rushed", "worker_rush", "cannon_rush", "cloak", "air"]

DATA_DIR = "data"
DATA_FILE = os.path.join(DATA_DIR, "strategies_protodd.json")


def _laplace(wl):
    wins, losses = wl[0], wl[1]
    return (wins + 1.0) / (wins + losses + 2.0)


class StrategyManager:
    def __init__(self, opponent_id, enemy_race_name="Random"):
        self.opponent_id = str(opponent_id) if opponent_id else "unknown"
        self.priority = RACE_PRIORITY.get(enemy_race_name, RACE_PRIORITY["Random"])
        self.all_data = self._load()
        self.record = self.all_data.get(self.opponent_id, {})
        self.profile = self.record.get("profile", {})
        self.observed = {}
        self.build = self._choose_build()
        self.aggression, self.aggression_mult = self._choose_aggression()
        self.reported = False

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

    def _choose_build(self):
        builds = self.record.get("builds", {})
        candidates = list(self.priority)
        if (
            RUSH_UNSAFE_BUILD
            and (self.expects("worker_rush") or self.expects("rushed"))
            and len(candidates) > 1
        ):
            candidates = [c for c in candidates if c != RUSH_UNSAFE_BUILD] or candidates

        def score(item):
            index, name = item
            return (_laplace(builds.get(name, [0, 0])), -index)

        _, best = max(enumerate(candidates), key=score)
        return best

    def _choose_aggression(self):
        record = self.record.get("aggr", {})

        def score(item):
            index, (name, _mult) = item
            return (_laplace(record.get(name, [0, 0])), -index)

        _, (name, mult) = max(enumerate(AGGRESSION_ARMS), key=score)
        return name, mult

    def report(self, won):
        if self.reported:
            return
        self.reported = True
        try:
            builds = self.record.setdefault("builds", {})
            wl = builds.setdefault(self.build, [0, 0])
            wl[0 if won else 1] += 1

            aggr = self.record.setdefault("aggr", {})
            awl = aggr.setdefault(self.aggression, [0, 0])
            awl[0 if won else 1] += 1

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

            self.record["last_build"] = self.build
            self.record["last_result"] = "win" if won else "loss"
            self.record["games"] = int(self.record.get("games", 0)) + 1
            self.all_data[self.opponent_id] = self.record
            self._save()
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
