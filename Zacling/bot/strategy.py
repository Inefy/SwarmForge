"""
Generative strategy learning for Zacling (v5).

There are no named builds anymore. The bot composes its own way to play each
game from independent learned dimensions - opening structure, gas timing,
aggression size, economic greed, tech focus - giving hundreds of possible
playstyles explored axis by axis. New opponents inherit global evidence while
matchup evidence gains weight with confidence; training explores more than ladder play.
"""

import json
import math
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
TRAINING_MODE = os.environ.get("SWARMFORGE_TRAINING") == "1"
EXPLORATION = 0.02 if TRAINING_MODE else 0.003
EPSILON = 0.05 if TRAINING_MODE else 0.005


def _laplace(wl):
    return (wl[0] + 1.0) / (wl[0] + wl[1] + 2.0)


class StrategyManager:
    def __init__(self, opponent_id, enemy_race_name="Random", map_name=None):
        self.opponent_id = str(opponent_id) if opponent_id else "unknown"
        self.enemy_race_name = enemy_race_name
        self.map_name = str(map_name) if map_name else "unknown_map"
        self.map_key = "map::" + self.map_name
        self.all_data = self._load()
        self.record = self._migrate(self.all_data.get(self.opponent_id, {}))
        self.global_record = self._migrate(self.all_data.get(GLOBAL_KEY, {}))
        self.map_record = self._migrate(self.all_data.get(self.map_key, {}))
        self.profile = self.record.get("profile", {})
        self.observed = {}

        self.choices = {}
        try:
            very_early_pressure = float(self.profile.get("first_pressure", 9999)) < 150
        except Exception:
            very_early_pressure = False
        risky = self.expects("worker_rush") or very_early_pressure
        for dim, arms in DIMS.items():
            candidates = list(arms)
            if risky and dim in RUSH_UNSAFE and len(candidates) > 1:
                trimmed = [a for a in candidates if a != RUSH_UNSAFE[dim]]
                if trimmed:
                    candidates = trimmed
            # Keep generated strategies internally coherent. Proxy/rush openings
            # cannot afford a capital-tech army plan before the game is decided.
            if dim == "army" and self.choices.get("pool_timing") == "pool12":
                candidates = ["swarm"]
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
        opp = self.record.get("dims", {}).get(dim, {})
        glob = self.global_record.get("dims", {}).get(dim, {})
        mp = self.map_record.get("dims", {}).get(dim, {})

        if random.random() < EPSILON:
            return random.choice(candidates)

        total_evidence = sum(
            sum(opp.get(name, [0, 0])) + 0.25 * min(40, sum(glob.get(name, [0, 0])))
            for name in candidates
        )
        global_totals = [0, 0]
        for name in candidates:
            wl = glob.get(name, [0, 0])
            global_totals[0] += wl[0]
            global_totals[1] += wl[1]
        global_baseline = _laplace(global_totals)

        def score(item):
            index, name = item
            opp_wl = opp.get(name, [0, 0])
            global_wl = glob.get(name, [0, 0])
            opp_trials = sum(opp_wl)
            global_trials = sum(global_wl)
            opponent_weight = min(0.9, opp_trials / (opp_trials + 8.0))
            global_rate = _laplace(global_wl)
            if not TRAINING_MODE and global_trials == 0:
                global_rate = max(0.0, global_baseline - 0.05)
            s = opponent_weight * _laplace(opp_wl) + (1.0 - opponent_weight) * global_rate
            # Per-map signal: what has worked on THIS map, growing with evidence.
            map_wl = mp.get(name, [0, 0])
            map_trials = sum(map_wl)
            if map_trials:
                map_weight = min(0.2, map_trials / (map_trials + 15.0))
                s = (1.0 - map_weight) * s + map_weight * _laplace(map_wl)
            evidence = opp_trials + 0.25 * min(40, global_trials)
            ucb_scale = 0.08 if TRAINING_MODE else 0.025
            s += ucb_scale * math.sqrt(math.log(total_evidence + 2.0) / (evidence + 1.0))
            s += random.uniform(0.0, EXPLORATION)
            s += (len(candidates) - index) * 0.002
            return s

        _, best = max(enumerate(candidates), key=score)
        return best

    def report(self, won, stats=None):
        if self.reported:
            return
        self.reported = True
        try:
            for record in (self.record, self.global_record, self.map_record):
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
                fp = float(self.observed["first_pressure"])
                samples = int(profile.get("pressure_samples", 0))
                previous = profile.get("first_pressure_ewma")
                ewma = fp if previous is None or samples == 0 else 0.8 * float(previous) + 0.2 * fp
                profile["first_pressure_ewma"] = ewma
                profile["first_pressure"] = ewma  # backwards-compatible field
                profile["pressure_samples"] = samples + 1

            self.record["last_result"] = "win" if won else "loss"
            self.all_data[self.opponent_id] = self.record
            self.all_data[GLOBAL_KEY] = self.global_record
            self.all_data[self.map_key] = self.map_record
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
