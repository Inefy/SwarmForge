"""
Regression self-test for the Batten bot family. Run before shipping or after any
edit - it loads all three bots against a lightweight SC2 stub and checks the
things that must never break: modules import, strategy learning is sound, and
the combat-micro helpers behave.

    py -3 selftest.py     ->  exits 0 if everything passes, 1 on any failure

No StarCraft II or burnysc2 install required; it stubs the sc2 API.
"""

import os
import sys
import tempfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
BOTS = [("Battencruiser", "Terran"), ("Zacling", "Zerg"), ("Protodd", "Protoss")]


def build_stub():
    stub = os.path.join(tempfile.gettempdir(), "sc2_selftest_stub")
    os.makedirs(os.path.join(stub, "sc2", "ids"), exist_ok=True)
    w = lambda p, s: open(os.path.join(stub, p), "w").write(s)
    w("sc2/__init__.py", "")
    w("sc2/bot_ai.py", "class BotAI:\n    pass\n")
    w("sc2/data.py", "import enum\nclass Result(enum.Enum):\n    Victory=1\n    Defeat=2\n    Tie=3\n")
    w("sc2/position.py",
      "import math\n"
      "class Point2(tuple):\n"
      "    def __new__(cls,xy): return super().__new__(cls,(float(xy[0]),float(xy[1])))\n"
      "    @property\n    def x(self): return self[0]\n"
      "    @property\n    def y(self): return self[1]\n"
      "    def towards(self,o,d):\n"
      "        ox,oy=(o.x,o.y) if hasattr(o,'x') else o\n"
      "        dx,dy=ox-self[0],oy-self[1]; n=math.hypot(dx,dy) or 1.0\n"
      "        return Point2((self[0]+dx/n*d,self[1]+dy/n*d))\n"
      "    def distance_to(self,o):\n"
      "        ox,oy=(o.x,o.y) if hasattr(o,'x') else o\n"
      "        return math.hypot(self[0]-ox,self[1]-oy)\n")
    w("sc2/ids/__init__.py", "")
    enum_src = ("class _E:\n"
                "    def __getattr__(self,n):\n"
                "        v=type('U',(),{'name':n})()\n"
                "        setattr(self,n,v)\n"
                "        return v\n")
    for m, c in [("unit_typeid", "UnitTypeId"), ("ability_id", "AbilityId"),
                 ("upgrade_id", "UpgradeId"), ("buff_id", "BuffId"), ("effect_id", "EffectId")]:
        w("sc2/ids/%s.py" % m, enum_src + "%s=_E()\n" % c)
    return stub


def make_fakes(Point2):
    class UList(list):
        def filter(self, f): return UList(x for x in self if f(x))
        def closest_to(self, u): return min(self, key=lambda e: e.distance_to(u))

    class Enemy:
        def __init__(self, tag, hp, x):
            self.tag = tag; self.health = hp; self.shield = 0; self._x = x
            self.type_id = type("T", (), {"name": "MARINE"})()
            self.can_attack_ground = True; self.can_attack_air = False
            self.is_armored = False; self.ground_range = 5; self.air_range = 0

        @property
        def position(self): return Point2((self._x, 0))

        def distance_to(self, o):
            ox = o.position.x if hasattr(o, "position") else o.x
            return abs(self._x - ox)

    class MyU:
        ground_dps = 6; air_dps = 0; ground_range = 5; air_range = 0

        def __init__(self, tag, x, hp_pct=1.0, cd=0):
            self.tag = tag; self._x = x; self.is_flying = False
            self.health_percentage = hp_pct; self.weapon_cooldown = cd

        @property
        def position(self): return Point2((self._x, 0))

        def target_in_range(self, e, bonus_distance=0): return abs(self._x - e._x) <= 6

        def calculate_damage_vs_target(self, t): return (6, 1, 5)

    return UList, Enemy, MyU


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_strategy(name, race):
    for m in [x for x in list(sys.modules) if x == "bot" or x.startswith("bot.")]:
        del sys.modules[m]
    sys.path.insert(0, os.path.join(HERE, name))
    import bot.strategy as S
    import json

    tmp = tempfile.mkdtemp()
    cwd = os.getcwd(); os.chdir(tmp); os.makedirs("data", exist_ok=True)
    # Redirect persistence into the sandbox - never touch real learning data.
    S.DATA_DIR = os.path.join(tmp, "data")
    S.DATA_FILE = os.path.join(S.DATA_DIR, "strategies_%s.json" % name.lower())
    S.GAME_LOG = os.path.join(S.DATA_DIR, "games_%s.jsonl" % name.lower())
    try:
        sm = S.StrategyManager("opp_x", race, "MapZ")
        check(set(sm.choices) == set(S.DIMS), "%s: choices != DIMS" % name)
        for d, a in sm.choices.items():
            check(a in S.DIMS[d], "%s: invalid arm %s=%s" % (name, d, a))

        free = next(d for d in S.DIMS if d not in set(S.RUSH_UNSAFE) and d != "aggression")
        good = S.DIMS[free][-1]
        for _ in range(70):
            mm = S.StrategyManager("opp_x", race, "MapZ")
            mm.report(mm.choices[free] == good, {"end_time": 400})
        c = Counter(S.StrategyManager("opp_x", race, "MapZ").choices[free] for _ in range(200))
        check(c.most_common(1)[0][0] == good, "%s: did not learn %s=%s (%s)" % (name, free, good, dict(c)))
        check(len(c) >= 2, "%s: exploration collapsed in %s" % (name, free))

        data = json.load(open("data/strategies_%s.json" % name.lower()))
        check("map::MapZ" in data, "%s: per-map record missing" % name)

        if S.RUSH_UNSAFE:
            dim, banned = next(iter(S.RUSH_UNSAFE.items()))
            json.dump({"early": {"profile": {"games": 5, "worker_rush": 5, "first_pressure": 55.0}}},
                      open("data/strategies_%s.json" % name.lower(), "w"))
            picks = Counter(S.StrategyManager("early", race, "MapZ").choices[dim] for _ in range(200))
            check(banned not in picks, "%s: rush-unsafe %s=%s not trimmed" % (name, dim, banned))
    finally:
        os.chdir(cwd); sys.path.pop(0)
    return len(S.DIMS)


def test_micro(name, Point2):
    for m in [x for x in list(sys.modules) if x == "bot" or x.startswith("bot.")]:
        del sys.modules[m]
    sys.path.insert(0, os.path.join(HERE, name))
    import bot.main as bm
    sys.path.pop(0)

    Cls = getattr(bm, name + "Bot")
    bot = Cls.__new__(Cls)
    bot._focus_board = {}; bot._point_order_cache = {}; bot.attack_mode = True
    bot.in_pathing_grid = lambda p: True; bot.time = 100.0
    bm.SPECIAL_TARGETS = set(); bm.WORKER_TYPES = set()
    UList, Enemy, MyU = make_fakes(Point2)

    enemies = UList([Enemy(1, 5, 3), Enemy(2, 45, 4)])
    bot._cached_enemies = enemies
    picks = [bot._best_target(MyU(t, 0), enemies).tag for t in (10, 11, 12)]
    check(picks == [1, 2, 2], "%s: focus fire overkills (%s)" % (name, picks))

    u = MyU(13, 0)
    bot._cached_enemies = UList([Enemy(9, 40, 10)])
    p = bot._concave_offset(u, Point2((20, 0)))
    check(abs(p.y) > 0.5, "%s: concave did not fan" % name)
    bot._cached_enemies = UList([Enemy(9, 40, 80)])
    check(bot._concave_offset(u, Point2((20, 0))) == Point2((20, 0)), "%s: concave fired when far" % name)

    fired = {}
    bot._flee = lambda unit, pos, dist: fired.setdefault("f", True)
    check(bot._preserve_hurt(MyU(20, 0, hp_pct=0.2, cd=1), UList([Enemy(30, 40, 1)])) is True and fired.get("f"),
          "%s: hurt unit not pulled" % name)
    check(bot._preserve_hurt(MyU(21, 0, hp_pct=0.9, cd=1), UList([Enemy(30, 40, 1)])) is False,
          "%s: healthy unit wrongly pulled" % name)


def main():
    # Test in training mode, where the bandit explores (ladder mode intentionally
    # exploits and would not converge in a short synthetic run).
    os.environ["SWARMFORGE_TRAINING"] = "1"
    stub = build_stub()
    sys.path.insert(0, stub)
    from sc2.position import Point2

    failures = []
    for name, race in BOTS:
        try:
            ndims = test_strategy(name, race)
            print("  [ok] %-13s strategy - %d dims learn/persist/trim" % (name, ndims))
        except Exception as e:
            failures.append("%s strategy: %s" % (name, e))
            print("  [FAIL] %-13s strategy - %s" % (name, e))
        try:
            test_micro(name, Point2)
            print("  [ok] %-13s micro    - focus fire / concave / preserve-hurt" % name)
        except Exception as e:
            failures.append("%s micro: %s" % (name, e))
            print("  [FAIL] %-13s micro    - %s" % (name, e))

    print("-" * 56)
    if failures:
        print("SELFTEST FAILED (%d):" % len(failures))
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("SELFTEST PASSED - all 3 bots import, learn, and micro correctly.")


if __name__ == "__main__":
    main()
