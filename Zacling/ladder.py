"""
Ladder harness for aiarena.net - adapted from the official python-sc2
examples/competitive/__init__.py template.
Connects the bot to a LadderManager-hosted game.
"""

import argparse
import asyncio

import aiohttp

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from sc2.client import Client
from sc2.main import _play_game
from sc2.portconfig import Portconfig

try:
    from sc2.protocol import ConnectionAlreadyClosedError
except ImportError:  # older python-sc2 releases use a different name
    from sc2.protocol import ConnectionAlreadyClosed as ConnectionAlreadyClosedError


def run_ladder_game(bot):
    # Load command line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("--GamePort", type=int, nargs="?", help="Game port")
    parser.add_argument("--StartPort", type=int, nargs="?", help="Start port")
    parser.add_argument("--LadderServer", type=str, nargs="?", help="Ladder server")
    parser.add_argument("--ComputerOpponent", type=str, nargs="?", help="Computer opponent")
    parser.add_argument("--ComputerRace", type=str, nargs="?", help="Computer race")
    parser.add_argument("--ComputerDifficulty", type=str, nargs="?", help="Computer difficulty")
    parser.add_argument("--OpponentId", type=str, nargs="?", help="Opponent ID")
    parser.add_argument("--RealTime", action="store_true", help="Real time flag")
    args, _unknown = parser.parse_known_args()

    host = "127.0.0.1" if args.LadderServer is None else args.LadderServer

    host_port = args.GamePort
    lan_port = args.StartPort

    # Add opponent_id to the bot class (accessed through self.opponent_id)
    bot.ai.opponent_id = args.OpponentId

    realtime = args.RealTime

    # Port config
    if lan_port is None:
        portconfig = None
    else:
        ports = [lan_port + p for p in range(1, 6)]

        portconfig = Portconfig()
        portconfig.server = [ports[1], ports[2]]
        portconfig.players = [[ports[3], ports[4]]]

    # Join ladder game
    g = join_ladder_game(host=host, port=host_port, players=[bot], realtime=realtime, portconfig=portconfig)

    # Run it
    result = asyncio.get_event_loop().run_until_complete(g)
    return result, args.OpponentId


async def join_ladder_game(host, port, players, realtime, portconfig, save_replay_as=None, game_time_limit=None):
    ws_url = "ws://{}:{}/sc2api".format(host, port)
    ws_connection = await aiohttp.ClientSession().ws_connect(ws_url, timeout=120)
    client = Client(ws_connection)
    try:
        result = await _play_game(players[0], client, realtime, portconfig, game_time_limit)
        if save_replay_as is not None:
            await client.save_replay(save_replay_as)
    except ConnectionAlreadyClosedError:
        logger.error("Connection was closed before the game ended")
        return None
    finally:
        await ws_connection.close()

    return result
