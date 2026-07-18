"""
Run ONE bot-vs-bot match in this process. Called by train.py in a subprocess
so a crashed game can never kill the training session.

Usage:
    python play_match.py --bot1 Battencruiser --bot2 Zacling --map AutomatonLE
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

BOTS = {
    "Battencruiser": "Terran",
    "Zacling": "Zerg",
    "Protodd": "Protoss",
}


def load_bot_class(name):
    """Import <name>/bot as an isolated package and return its bot class."""
    for mod in [m for m in list(sys.modules) if m == "bot" or m.startswith("bot.")]:
        del sys.modules[mod]
    folder = os.path.join(HERE, name)
    sys.path.insert(0, folder)
    try:
        import bot.main as bot_main
        cls = getattr(bot_main, name + "Bot")
    finally:
        sys.path.pop(0)
    # Detach so the next load_bot_class gets a fresh package.
    for mod in [m for m in list(sys.modules) if m == "bot" or m.startswith("bot.")]:
        del sys.modules[mod]
    return cls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot1", required=True, choices=sorted(BOTS))
    opponent = parser.add_mutually_exclusive_group(required=True)
    opponent.add_argument("--bot2", choices=sorted(BOTS))
    opponent.add_argument("--computer-race", choices=["Terran", "Zerg", "Protoss", "Random"],
                          help="Play vs the built-in non-cheating Very Hard AI")
    parser.add_argument("--map", required=True)
    parser.add_argument("--game-time-limit", type=int, default=10800,
                        help="Max game duration in in-game seconds (default 3 game-hours)")
    args = parser.parse_args()

    # Learning data is written to ./data relative to cwd - keep it at the arena root.
    os.chdir(HERE)

    from sc2 import maps
    from sc2.data import Difficulty, Race, Result
    from sc2.main import run_game
    from sc2.player import Bot, Computer

    cls1 = load_bot_class(args.bot1)
    ai1 = cls1()
    if args.computer_race:
        # Stable ID per AI race so learning vs the Elite AI accumulates too.
        ai1.opponent_id = "ai_elite_" + args.computer_race.lower()
        opponent = Computer(Race[args.computer_race], Difficulty.VeryHard)
        opponent_name = "AI_" + args.computer_race
    else:
        cls2 = load_bot_class(args.bot2)
        ai2 = cls2()
        ai1.opponent_id = "arena_" + args.bot2
        ai2.opponent_id = "arena_" + args.bot1
        opponent = Bot(Race[BOTS[args.bot2]], ai2, name=args.bot2)
        opponent_name = args.bot2

    try:
        result = run_game(
            maps.get(args.map),
            [Bot(Race[BOTS[args.bot1]], ai1, name=args.bot1), opponent],
            realtime=False,
            game_time_limit=args.game_time_limit,
        )
    except Exception:
        # Known python-sc2 flake: one client died and run_game asserts on the
        # partial result list. Report an unknown result instead of crashing
        # the whole match slot (train.py logs it without counting standings).
        import traceback
        traceback.print_exc()
        result = "Unknown"

    def norm(r):
        try:
            return r.name  # Result enum
        except Exception:
            return str(r)

    if isinstance(result, list):
        r1, r2 = norm(result[0]), norm(result[1])
    else:
        r1 = norm(result)
        r2 = {"Victory": "Defeat", "Defeat": "Victory", "Tie": "Tie"}.get(r1, "Unknown")
    print("RESULT_JSON " + json.dumps(
        {"bot1": args.bot1, "bot2": opponent_name, "map": args.map, "result1": r1, "result2": r2}
    ))


if __name__ == "__main__":
    main()
