# Battencruiser

Adaptive Terran bot for the [aiarena.net](https://aiarena.net) StarCraft II bot ladder,
built on [python-sc2 (burnysc2)](https://github.com/BurnySc2/python-sc2).

## How it plays

Three builds, picked **per opponent** from a persistent win/loss record
(aiarena keeps the `data/` folder between games and tells the bot who it's playing):

| Build | What it is |
|---|---|
| `three_rax` | 3-barracks marine/stim timing attack, transitions into macro (default vs unknown opponents) |
| `bio_macro` | 1-rax expand into multi-base marine/marauder/medivac/tank with +weapons/+armor |
| `proxy_2rax` | Proxy barracks marine all-in with an 8-SCV pull |

Three learning systems run across games (this is how the top ladder bots work):

1. **Build bandit** - win/loss per build per opponent; abandons builds an opponent
   counters, sticks with what wins.
2. **Timing bandit** - learns per opponent whether to attack early, on time, or
   late with a bigger army.
3. **Opponent fingerprinting** - records rushes, worker rushes, cannon rushes,
   cloak, air comps and how early pressure arrives. The next game starts
   pre-adapted: bunkers before the rush exists, turrets before the banshee,
   vikings before the carriers, and it won't proxy a known worker-rusher.

In-game it also adapts continuously: marauder/marine mix follows the enemy's
armored/light ratio, viking count scales with enemy capital ships, and the army
spreads out the moment splash damage appears on the field.

Combat engine: range-aware stutter-step micro (kites melee and shorter-ranged units,
closes on longer-ranged ones), target priority (banelings/casters/siege units first,
lowest HP focus fire), dodges psi storms / corrosive biles / nukes / lurker spikes /
liberator zones / banelings, fight-or-flee power evaluation (disengages losing fights,
regroups, re-engages), reinforcement grouping, stim management, medivac boost-follow,
tank siege control, automatic viking production against air-heavy opponents.

Defense & infrastructure: ramp wall with depot raise/lower and SCV repair, bunkers
against detected rushes, rush / worker-rush / cannon-rush response, SCV scout,
orbital MULEs + scans against cloak, planetary fortresses on outlying bases,
missile turrets at every mining base, adaptive frame-skip under CPU load.

## 1. Setup (once)

1. Install StarCraft II (free) via Battle.net.
2. Install Python 3.9+ from python.org (check "Add to PATH").
3. Double-click `setup.bat` (installs the burnysc2 library).
4. Download the current season maps from https://aiarena.net/wiki/maps/ and unzip
   every `.SC2Map` into `C:\Program Files (x86)\StarCraft II\Maps`
   (create the `Maps` folder if needed).

## 2. Test locally

Double-click `test_game.bat`, or from a terminal in this folder:

    py -3 run.py --race zerg --difficulty veryhard
    py -3 run.py --race protoss --difficulty veryhard --build proxy_2rax
    py -3 run.py --map AutomatonLE --race terran --realtime

`--difficulty veryhard` is the in-game "Elite" AI. `--build` forces a specific build
so you can watch each one. Add `--realtime` to watch at normal speed.

## 3. Upload to the ladder

1. Run:  `py -3 make_ladder_zip.py`  → creates `Battencruiser.zip`
   (bundles your installed sc2 library, as the aiarena wiki recommends).
2. Register at https://aiarena.net/accounts/register/
3. Upload the zip at https://aiarena.net/botupload/ — name `Battencruiser`,
   race Terran, type `python`.
4. Opt into the current competition from your bot's page, and it starts playing.

Rename the bot by editing `BOT_NAME` in `run.py`, the name in `ladderbots.json`,
and `ZIP_NAME` in `make_ladder_zip.py`.

## Tuning

- Build order priority for unknown opponents: `BUILDS` in `bot/strategy.py`.
- Timings, unit caps, attack sizes: `build_config()` in `bot/main.py`.
- Micro behavior: `_micro_bio`, `_micro_medivac`, `_micro_tank` in `bot/main.py`.

Between ladder games you can download the bot's accumulated opponent memory
(`data/strategies.json`) from your aiarena profile page.
