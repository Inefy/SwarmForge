"""
Entry point for Zacling.

On the aiarena ladder this is started with --LadderServer and joins the
hosted game. Run it directly (no args) for a local test game against the
built-in AI:

    python run.py --race zerg --difficulty veryhard --map AutomatonLE
"""

import argparse
import sys

from sc2.data import Difficulty, Race
from sc2.player import Bot, Computer

from bot.main import ZaclingBot

BOT_NAME = "Zacling"

# Maps tried in order for local games (whatever is installed wins).
LOCAL_MAP_CANDIDATES = [
    "AbyssalReefAIE",
    "AcropolisAIE",
    "AutomatonAIE",
    "EphemeronAIE",
    "InterloperAIE",
    "ThunderbirdAIE",
    "PylonAIE",
    "TorchesAIE",
    "PersephoneAIE",
    "AutomatonLE",
    "AcropolisLE",
    "AbyssalReefLE",
]

RACES = {
    "terran": Race.Zerg,
    "zerg": Race.Zerg,
    "protoss": Race.Protoss,
    "random": Race.Random,
}

DIFFICULTIES = {
    "easy": Difficulty.Easy,
    "medium": Difficulty.Medium,
    "hard": Difficulty.Hard,
    "harder": Difficulty.Harder,
    "veryhard": Difficulty.VeryHard,  # "Elite" in the game UI
    "cheatvision": Difficulty.CheatVision,
    "cheatmoney": Difficulty.CheatMoney,
    "cheatinsane": Difficulty.CheatInsane,
}


def make_bot():
    return Bot(Race.Zerg, ZaclingBot(), name=BOT_NAME)


def run_local():
    from sc2 import maps
    from sc2.main import run_game

    parser = argparse.ArgumentParser()
    parser.add_argument("--map", type=str, default=None, help="Exact map name (without .SC2Map)")
    parser.add_argument("--race", type=str, default="zerg", choices=sorted(RACES))
    parser.add_argument("--difficulty", type=str, default="veryhard", choices=sorted(DIFFICULTIES))
    parser.add_argument("--realtime", action="store_true", help="Watch in real time")
    parser.add_argument("--force", type=str, default=None,
                        help="Force learned dims, e.g. --force production=3rax,location=proxy")
    args, _unknown = parser.parse_known_args()

    candidates = [args.map] if args.map else LOCAL_MAP_CANDIDATES
    game_map = None
    for name in candidates:
        try:
            game_map = maps.get(name)
            break
        except Exception:
            continue
    if game_map is None:
        print("No known ladder map found in your StarCraft II/Maps folder.")
        print("Download maps from https://aiarena.net/wiki/maps/ and unzip them into")
        print(r"  C:\Program Files (x86)\StarCraft II\Maps")
        print("or pass --map <ExactMapName>.")
        sys.exit(1)

    bot = make_bot()
    if args.force:
        import bot.strategy as strategy_module
        forced = dict(kv.split("=", 1) for kv in args.force.split(","))
        unknown = {d: a for d, a in forced.items()
                   if d not in strategy_module.DIMS or a not in strategy_module.DIMS[d]}
        if unknown:
            print("Unknown dims/arms:", unknown)
            print("Valid:", strategy_module.DIMS)
            sys.exit(1)
        original_choose = strategy_module.StrategyManager._choose

        def forced_choose(self, dim, candidates, _orig=original_choose, _f=forced):
            if dim in _f:
                return _f[dim]
            return _orig(self, dim, candidates)

        strategy_module.StrategyManager._choose = forced_choose
        print("Forcing:", forced)

    print("Map: {} | vs {} {}".format(game_map.name, args.difficulty, args.race))
    run_game(
        game_map,
        [bot, Computer(RACES[args.race], DIFFICULTIES[args.difficulty])],
        realtime=args.realtime,
    )


if __name__ == "__main__":
    if "--LadderServer" in sys.argv:
        from ladder import run_ladder_game

        print("Starting ladder game...")
        result, opponent_id = run_ladder_game(make_bot())
        print(result, " against opponent ", opponent_id)
    else:
        run_local()
