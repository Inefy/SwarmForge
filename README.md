# SwarmForge — The Batten Bot Family

SwarmForge is a self-play laboratory for three adaptive StarCraft II bots:
Battencruiser, Zacling, and Protodd.

Three aiarena.net StarCraft II ladder bots sharing one adaptive architecture:

| Bot | Race | Builds |
|---|---|---|
| **Battencruiser** | Terran | learned bio, mech, air, mixed, and proxy plans |
| **Zacling** | Zerg | learned swarm, ranged, air, hive, and rush plans |
| **Protodd** | Protoss | learned gateway, robotics, fleet, mixed, and proxy plans |

Every bot has confidence-aware per-opponent learning, threat fingerprinting
with pre-adaptation, fight-or-flee combat evaluation, effect/baneling dodging,
range-aware stutter micro, specialist spellcasting, reactive counter production,
and complete ground/air/late-tech upgrade paths.

## What the bots learn (v5 - generative openings)

Independent dimensions are learned per opponent (thousands of combinations),
each scored from global and opponent-specific evidence. New opponents inherit
the globally strongest plan; matchup evidence gradually receives up to 90% of
the weight. Training uses stronger confidence exploration, while ladder mode
uses only 0.5% deliberate exploration per dimension:

* **opening structure** - composed from parameters, not scripts: production
  count (1/2/3 rax, 1/2/4 gates, pool at 12/16/hatch-first), home vs proxy
  location, and gas timing - hundreds of emergent openings per bot
* **aggression** - attack at 0.55x to 1.7x the normal army size
* **greed** - worker counts and expansion timing (lean / standard / greedy)
* **tech** - composition focus (e.g. marauder_bio, ling_flood, chargelot)
* **army plan** - full race tech-tree exploration: bio/mech/sky, gateway/robo/fleet,
  and swarm/ranged/air/hive compositions, including specialist and capital units

They also adapt in-game (repelled attacks raise the next attack's size bar,
unit mix follows the enemy's armored/light/air ratios, and urgent counters can
override a learned composition) and fingerprint opponents between games.
Every game is logged to `data/games_<bot>.jsonl`.

Run `py -3 analyze.py` after training for standings, matchup grids,
per-dimension winrates, and each bot's current best answer per opponent.

## Self-play training arena

`train.py` runs round-robin self-play plus a configurable share of games against
the built-in Very Hard AI for opponent diversity. Each match is isolated in a
subprocess; timed-out process trees and new SC2 children are cleaned up, while
crash output is retained in `crashes.log`.

    py -3 train.py --hours 8        (or double-click train.bat)
    py -3 train.py --games 30       quick session
    py -3 train.py --map AutomatonLE --hours 2
    py -3 train.py --hours 8 --ai-share 0.35

Matches allow up to three in-game hours (`10800` game seconds). Because games
run faster than real time, the arena uses a 60-minute real-time safety timeout
for a stuck match while the overall `--hours` setting remains wall-clock time.

Requirements: StarCraft II installed, `pip install burnysc2`, and ladder maps
from https://aiarena.net/wiki/maps/ in your StarCraft II\Maps folder.
Bot-vs-bot runs two SC2 clients at once - expect a game every few minutes.

Progress: `training_log.csv` + live standings in the console. Stop any time
with Ctrl+C; learning is saved after every game. At session end the learned
data is copied into each bot's `data/` folder so `make_ladder_zip.py` ships
the trained knowledge to the ladder.

## Shipping to aiarena.net

In each bot folder: `py -3 make_ladder_zip.py`, then upload the zip at
https://aiarena.net/botupload/ (register first). Upload all three - they'll
each keep learning on the ladder via aiarena's persistent data folders.
