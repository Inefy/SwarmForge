"""
Generative strategy learning for Zacling (v5).

There are no named builds anymore. The bot composes its own way to play each
game from independent learned dimensions - opening structure, gas timing,
aggression size, economic greed, tech focus - giving hundreds of possible
playstyles explored axis by axis. Arms are scored 65% vs this opponent and
35% globally, with exploration noise so it never stops inventing.
"""

import json
import os
import random
import tempfile
import time

DIMS = {'pool_timing': ['pool16', 'pool12', 'hatch_first'], 'gas_open': ['one_gas', 'no_gas', 'two_gas'], 'aggression': ['standard', 'early', 'very_early', 'late', 'very_late'], 'greed': ['standard', 'greedy', 'lean'], 'tech': ['roach_focus', 'hydra_focus', 'ling_flood'], 'army': ['swarm', 'ranged', 'sky', 'hive']}

AGGRESSION_MULTS = {"standard": 1.0, "early": 0.75, "very_early": 0.55, "late": 1.3, "very_late": 1.7}
GREED_PARAMS = {"standard": (1.0, 0), "greedy": (1.15, 50), "lean": (0.85, -60)}
RUSH_UNSAFE = {'pool_timing': 'hatch_first'}
OPENING_MIGRATION = {'roach_timing': {'pool_timing': 'pool16'}, 'macro_hydra': {'pool_timing': 'pool16'}, 'twelve_pool': {'pool_timing': 'pool12'}}
PROFILE_FLAGS = ["rushed", "worker_rush", "cannon_rush", "cloak", "air"]

DATA_DIR = "data"
DATA_FILE = os.path.join(DATA_DIR, "strategies_zacling.json")
GAME_LOG = os.path.join(DATA_DIR, "games_zacling.jsonl")
GLOBAL_KEY = "__global__"
EXPLORATION = 0.10   # noise on scores (tie-breaking / soft exploration)
EPSILON = 0.12       # chance per dimension to try a fully random arm


def _laplace(wl):
    return (wl[0] + 1.0) / (wl[0] + wl[1] + 2.0)


class StrategyManager:
    def __init__(self, opponent_id, enemy_race_name="Random"):
        self.opponent_id = str(opponent_id) if opponent_id else "unknown"
        self.enemy_race_name = enemy_race_name
        self.all_data = self._load()
        self.record = self._migrate(self.all_data.get(self.opponent_id, {}))
        self.global_record = self._migrate(self.all_data.get(GLOBAL_KEY, {}))
        self.profile = self.record.get("profile", {})
        self.observed = {}

        self.choices = {}
        risky = self.expects("worker_rush") or self.expects("rushed")
        for dim, arms in DIMS.items():
            candidates = list(arms)
            if risky and dim in RUSH_UNSAFE and len(candidates) > 1:
                trimmed = [a for a in candidates if a != RUSH_UNSAFE[dim]]
                if trimmed:
                    candidates = trimmed
            self.choices[dim] = self._choose(dim, candidates)
            setattr(self, dim, self.choices[dim])
        self.aggression_mult = AGGRESSION_MULTS.get(self.choices.get("aggression"), 1.0)
        greed = GREED_PARAMS.get(self.choices.get("greed"), (1.0, 0))
        self.greed_worker_mult, self.greed_expand_shift = greed
        # Human-readable label for logs.
        self.build = "/".join(self.choices[d] for d in DIMS)
        self.opening = self.build
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

    @staticmethod
    def _migrate(record):
        """v3 builds/aggr -> v4 dims -> v5 seeded generative dims."""
        try:
            dims = record.setdefault("dims", {})
            if "builds" in record and "opening" not in dims:
                dims["opening"] = record["builds"]
            if "aggr" in record and "aggression" not in dims:
                dims["aggression"] = record["aggr"]
            record.pop("builds", None)
            record.pop("aggr", None)
            old_openings = dims.pop("opening", None)
            if old_openings:
                for old_arm, wl in old_openings.items():
                    for dim, arm in OPENING_MIGRATION.get(old_arm, {}).items():
                        slot = dims.setdefault(dim, {}).setdefault(arm, [0, 0])
                        slot[0] += wl[0]
                        slot[1] += wl[1]
        except Exception:
            pass
        return record

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

    def _choose(self, dim, candidates):
        # Guaranteed exploration: never stop testing alternatives.
        if random.random() < EPSILON:
            return random.choice(candidates)
        opp = self.record.get("dims", {}).get(dim, {})
        glob = self.global_record.get("dims", {}).get(dim, {})

        def score(item):
            index, name = item
            s = 0.65 * _laplace(opp.get(name, [0, 0])) + 0.35 * _laplace(glob.get(name, [0, 0]))
            s += random.uniform(0.0, EXPLORATION)
            s += (len(candidates) - index) * 0.005
            return s

        _, best = max(enumerate(candidates), key=score)
        return best

    def report(self, won, stats=None):
        if self.reported:
            return
        self.reported = True
        try:
            for record in (self.record, self.global_record):
                dims = record.setdefault("dims", {})
                for dim, arm in self.choices.items():
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
            self._log_game(won, stats)
        except Exception:
            pass

    def _log_game(self, won, stats):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            entry = {
                "ts": int(time.time()),
                "opponent": self.opponent_id,
                "enemy_race": self.enemy_race_name,
                "won": bool(won),
            }
            entry.update(self.choices)
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
