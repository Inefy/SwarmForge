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

The default `focus` schedule trains each workspace bot against every imported
bot (42 pairings per round). To include imported-vs-imported and workspace
self-play, use `--include-self-play`. To schedule every unordered pair, use
`--all` (136 pairings per round).

## Run training

WSL2 Ubuntu must have access to Docker. The current setup can be checked with:

```powershell
wsl -d Ubuntu -- bash -lc "docker version"
```

Then run a bounded batch:

```powershell
py -3 ladder/train_ladder.py --rounds 1 --games 10
py -3 ladder/train_ladder.py --rounds 1 --all --games 50
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

The first Docker run downloads the AI Arena local-play images, so it can take
some time and several gigabytes of disk space. The Windows SC2 installation is
used as the map source; it is not executed directly inside WSL.
