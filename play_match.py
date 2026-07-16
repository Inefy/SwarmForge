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
    parser.add_argument("--bot2", required=True, choices=sorted(BOTS))
    parser.add_argument("--map", required=True)
    parser.add_argument("--game-time-limit", type=int, default=1800,
                        help="Max game duration in in-game seconds (default 30 game-minutes)")
    args = parser.parse_args()

    # Learning data is written to ./data relative to cwd - keep it at the arena root.
    os.chdir(HERE)

    from sc2 import maps
    from sc2.data import Race, Result
    from sc2.main import run_game
    from sc2.player import Bot

    cls1 = load_bot_class(args.bot1)
    cls2 = load_bot_class(args.bot2)
    ai1, ai2 = cls1(), cls2()
    # Stable opponent IDs so the per-opponent learning applies across the session.
    ai1.opponent_id = "arena_" + args.bot2
    ai2.opponent_id = "arena_" + args.bot1

    result = run_game(
        maps.get(args.map),
        [
            Bot(Race[BOTS[args.bot1]], ai1, name=args.bot1),
            Bot(Race[BOTS[args.bot2]], ai2, name=args.bot2),
        ],
        realtime=False,
        game_time_limit=args.game_time_limit,
    )

    def norm(r):
        try:
            return r.name  # Result enum
        except Exception:
            return str(r)

    if isinstance(result, list):
        r1, r2 = norm(result[0]), norm(result[1])
    else:
        r1, r2 = norm(result), "Unknown"
    print("RESULT_JSON " + json.dumps(
        {"bot1": args.bot1, "bot2": args.bot2, "map": args.map, "result1": r1, "result2": r2}
    ))


if __name__ == "__main__":
    main()
