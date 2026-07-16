"""
Build the aiarena.net upload zip for Battencruiser.

Run AFTER `pip install --upgrade burnysc2`. Bundles your installed sc2 library
and any learned strategy data in ./data (so trained knowledge ships with the bot).

Usage:  python make_ladder_zip.py   ->  Battencruiser.zip
"""

import os
import sys
import zipfile

BOT_FILES = ["run.py", "ladder.py", "ladderbots.json", "requirements.txt"]
BOT_DIRS = ["bot"]
ZIP_NAME = "Battencruiser.zip"
MAX_MB = 50


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)
    try:
        import sc2
    except ImportError:
        print("ERROR: python-sc2 is not installed. Run:  pip install --upgrade burnysc2")
        sys.exit(1)
    sc2_dir = os.path.dirname(os.path.abspath(sc2.__file__))
    print("Bundling sc2 library from:", sc2_dir)

    if os.path.exists(ZIP_NAME):
        os.remove(ZIP_NAME)

    with zipfile.ZipFile(ZIP_NAME, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in BOT_FILES:
            if not os.path.exists(f):
                print("ERROR: missing", f)
                sys.exit(1)
            zf.write(f, f)
        for d in BOT_DIRS:
            for root, _dirs, files in os.walk(d):
                if "__pycache__" in root:
                    continue
                for f in files:
                    if f.endswith(".pyc"):
                        continue
                    path = os.path.join(root, f)
                    zf.write(path, path)
        # Ship the data dir INCLUDING any learned strategies from training.
        wrote_data = False
        if os.path.isdir("data"):
            for f in os.listdir("data"):
                if f.endswith(".json"):
                    zf.write(os.path.join("data", f), os.path.join("data", f))
                    wrote_data = True
                    print("Including learned data:", f)
        if not wrote_data:
            zf.writestr(zipfile.ZipInfo("data/"), "")
        for root, _dirs, files in os.walk(sc2_dir):
            if "__pycache__" in root:
                continue
            for f in files:
                if f.endswith(".pyc"):
                    continue
                path = os.path.join(root, f)
                rel = os.path.join("sc2", os.path.relpath(path, sc2_dir))
                zf.write(path, rel)

    size_mb = os.path.getsize(ZIP_NAME) / (1024.0 * 1024.0)
    print("Created {} ({:.1f} MB)".format(ZIP_NAME, size_mb))
    if size_mb > MAX_MB:
        print("WARNING: exceeds the {} MB aiarena limit!".format(MAX_MB))
    else:
        print("Upload it at: https://aiarena.net/botupload/")


if __name__ == "__main__":
    main()
