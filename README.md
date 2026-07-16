# SwarmForge — The Batten Bot Family

SwarmForge is a self-play laboratory for three adaptive StarCraft II bots:
Battencruiser, Zacling, and Protodd.

Three aiarena.net StarCraft II ladder bots sharing one adaptive architecture:

| Bot | Race | Builds |
|---|---|---|
| **Battencruiser** | Terran | 3-rax stim timing / bio+medivac+tank macro / proxy 2-rax |
| **Zacling** | Zerg | roach timing / 3-base roach-hydra / 12-pool speedlings |
| **Protodd** | Protoss | 4-gate warp rush / stalker-immortal macro / proxy gates |

Every bot has per-opponent persistent learning (build bandit, attack-timing
bandit, threat fingerprinting with pre-adaptation), fight-or-flee combat
evaluation, effect/baneling dodging, range-aware stutter micro, and splash
spreading. See each bot folder's README/source for details.

## Self-play training arena

`train.py` runs the three bots against each other for hours, round-robin,
each match in an isolated subprocess. Every game updates each bot's learning
files, so they adapt to each other all night: builds that lose get abandoned,
attack timings get tuned, and scouted threats get pre-countered next game.

    py -3 train.py --hours 8        (or double-click train.bat)
    py -3 train.py --games 30       quick session
    py -3 train.py --map AutomatonLE --hours 2

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
