# SwarmForge bot ladder

This directory turns the three workspace bots and the imported bots in
`trainingbots/` into a repeatable local ladder. The generated `ladder/bots/`
directory is ignored by Git; the zip files remain the source of truth.

## Prepare and inspect

From the repository root:

```powershell
py -3 ladder/prepare_ladder.py
py -3 ladder/train_ladder.py --dry-run
```

The default schedule uses the 12 locally compatible bots: the three workspace
bots and nine imported opponents. `focus` gives 27 workspace-vs-imported
pairings per round; `--all` gives 66 unique pairings. Five packages that failed
every local compatibility test remain in the manifest but are quarantined.
Use `--include-disabled` only for diagnostics.

## Run training

WSL2 Ubuntu must have access to Docker. The current setup can be checked with:

```powershell
wsl -d Ubuntu -- bash -lc "docker version"
```

Then run a bounded batch:

```powershell
py -3 ladder/train_ladder.py --rounds 1 --games 10
py -3 ladder/train_ladder.py --rounds 1 --all --games 50
py -3 ladder/train_ladder.py --all --bot Battencruiser --bot Mulebot --games 1
```

For an ongoing session, use a time budget. Stop it with Ctrl+C; bot data is
written to the repository `data/` directory and remains available to later
batches:

```powershell
py -3 ladder/train_ladder.py --hours 8 --all
```

The runner uses the maps at `E:\games\StarCraft II\Maps`, exposed in WSL as
`/mnt/e/games/StarCraft II/Maps`. Override that location with `--sc2-maps`.
Replays are written to `ladder/replays/`, logs to `ladder/logs/`, and the
batch history is kept in `ladder/results-history.jsonl`.

The imported bots are opponents; they are not modified. Workspace bots are
started with `SWARMFORGE_TRAINING=1` and share `data/` through the container's
`/training-data` mount. An imported bot can persist its own learning only if
its code writes state into that mounted directory.

Each match has a 15-minute controller limit (`MAX_REAL_TIME = 900`). The runner
also watches the match and results files; after 20 minutes without progress it
restarts the containers, records `WatchdogTimeout`, skips that pairing, and
continues. `--hours` is enforced as a hard session deadline even mid-round.

The first Docker run downloads the AI Arena local-play images, so it can take
some time and several gigabytes of disk space. The Windows SC2 installation is
used as the map source; it is not executed directly inside WSL.
