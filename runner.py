"""প্রতি 15 মিনিটে একটি চক্র চালায়। GitHub-এ SHARDS-সংখ্যক রানার একসাথে চলে, প্রত্যেকে নিজের ভাগের টুকরো তোলে।
একটি রান ~5 ঘণ্টা 40 মিনিট চলে, তারপর পরের রান দায়িত্ব নেয়।

  python runner.py --init          # once per GitHub run, before the shards: creates/checks data/state.json
  python runner.py --shard 3       # one parallel worker
  python runner.py --shard 0 --once  # take a single snapshot now (handy for testing on your PC)

All shards share one fixed 15-minute grid that starts at a quarter-hour (:00, :15, :30, :45 Dhaka time).
"""
import argparse
import json
import math
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
import collector
from collector import DATA, ROOT, upload_to_drive

TZ = ZoneInfo(config.TIMEZONE)
STATE = DATA / "state.json"


def now():
    return datetime.now(TZ)


def archive_old_study(old_version):
    """Moves data from an earlier study version aside, so the new 30 days start clean."""
    import shutil
    dest = DATA / f"old_v{old_version}"
    dest.mkdir(parents=True, exist_ok=True)
    for item in list(DATA.iterdir()):
        if item.name.startswith("old_v"):
            continue
        shutil.move(str(item), str(dest / item.name))
    print(f"Settings changed: previous data moved to data/old_v{old_version}. Starting a fresh 30 days.")


def next_quarter_hour(t):
    t = t.replace(second=0, microsecond=0)
    return t + timedelta(minutes=(15 - t.minute % 15) % 15 or 0)


def init_state():
    """Creates data/state.json (or archives an older study version). Returns the state."""
    DATA.mkdir(exist_ok=True)
    if STATE.exists():
        s = json.loads(STATE.read_text())
        if s.get("version", 1) == config.STUDY_VERSION:
            return s
        archive_old_study(s.get("version", 1))
    start = next_quarter_hour(now() + timedelta(minutes=4))  # leave time for the shards to install
    s = {"start": start.isoformat(), "finished": False, "version": config.STUDY_VERSION,
         "zoom": config.ZOOM, "shards": config.SHARDS, "bounds": config.BOUNDS}
    STATE.write_text(json.dumps(s, indent=1))
    print(f"New study: first snapshot at {start:%d %b %Y %H:%M} Dhaka time, {config.COLLECTION_DAYS} days.")
    return s


def progress_file(shard):
    return collector.SHARD_DIR / f"progress_{shard:02d}.json"


def load_progress(shard):
    f = progress_file(shard)
    return json.loads(f.read_text()) if f.exists() else {"last_slot": -1, "drive_bytes": 0}


def git_push(message, paths):
    """Commit and push; several shards push at the same time, so retry with a short random wait."""
    paths = [p for p in paths if (ROOT / p).exists()]
    if not paths:
        return
    subprocess.run(["git", "add", "-A", *paths], cwd=ROOT)
    if subprocess.run(["git", "commit", "-q", "-m", message], cwd=ROOT, capture_output=True).returncode != 0:
        return  # nothing to commit
    for attempt in range(8):
        subprocess.run(["git", "pull", "-q", "--rebase", "-X", "theirs"], cwd=ROOT, capture_output=True)
        r = subprocess.run(["git", "push", "-q"], cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            return
        time.sleep(random.uniform(3, 15))
    print(f"git push gave up: {r.stderr.strip()[:300]}")


def run_analysis():
    r = subprocess.run([sys.executable, "analyze.py"], cwd=ROOT, capture_output=True, text=True)
    print(r.stdout.strip()[-300:] or r.stderr.strip()[-300:])


def backup_data_to_drive():
    """Copies the combined CSV files to Drive (folder: data), replacing the older copy."""
    for name in ("snapshots.csv", "thana.csv", "daily_summary.csv", "hotspots.csv"):
        f = DATA / name
        if f.exists():
            upload_to_drive(name, "", f.read_bytes(), "text/csv", overwrite=True, subfolder="data")


def run_shard(shard, once=False):
    state = json.loads(STATE.read_text()) if STATE.exists() else init_state()
    start = datetime.fromisoformat(state["start"])
    end = start + timedelta(days=config.COLLECTION_DAYS)
    deadline = now() + timedelta(minutes=config.RUN_MINUTES)
    step = config.INTERVAL_SECONDS
    prog = load_progress(shard)
    my_paths = [f"data/shards/shard_{shard:02d}.csv", f"data/shards/shard_{shard:02d}_thana.csv",
                f"data/shards/progress_{shard:02d}.json"]
    if shard == 0:
        my_paths += ["data", "docs", "snapshots"]
    print(f"Shard {shard}/{config.SHARDS}: {len(collector.shard_tiles(shard))} tiles per cycle. "
          f"Study {start:%d %b %H:%M} → {end:%d %b %H:%M}. This run stops by {deadline:%H:%M}.")

    while True:
        t = now()
        if t >= end:
            if shard == 0:
                state["finished"] = True
                STATE.write_text(json.dumps(state, indent=1))
                run_analysis()
                backup_data_to_drive()
                git_push("30-day collection finished", my_paths)
            print("30 days complete. Collection stopped.")
            return
        elapsed = (t - start).total_seconds()
        slot = max(prog["last_slot"] + 1, math.ceil(elapsed / step - 1e-6), 0)
        prev = slot - 1
        if prev > prog["last_slot"] and prev >= 0 and elapsed - prev * step < 120:
            slot = prev  # only slightly late for the previous slot: take it now
        slot_time = start + timedelta(seconds=slot * step)
        if once:
            slot_time = now()
        elif slot_time > deadline:
            print("Run time used up; handing over to the next run.")
            return
        wait = (slot_time - now()).total_seconds()
        if wait > 0:
            time.sleep(wait)

        t0 = time.time()
        try:
            r = collector.run_cycle(slot_time.replace(microsecond=0), slot, shard)
            prog["drive_bytes"] += r["bytes"]
            print(f"[slot {slot}] {slot_time:%Y-%m-%d %H:%M} shard {shard}: tiles {r['tiles_ok']}/{r['tiles']} "
                  f"score={r['score']} drive={r['drive']} took={time.time() - t0:.0f}s "
                  f"stored={prog['drive_bytes'] / 1e9:.2f}GB")
            if time.time() - t0 > step:
                print("Warning: this cycle took longer than 15 minutes. Increase SHARDS in config.py.")
        except Exception as e:
            print(f"[slot {slot}] failed: {e}")
        prog["last_slot"] = slot
        progress_file(shard).write_text(json.dumps(prog))
        if shard == 0:
            run_analysis()
            if slot % 4 == 0:  # about once an hour
                backup_data_to_drive()
        git_push(f"slot {slot} shard {shard}", my_paths)
        if once:
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    if a.init:
        s = init_state()
        print("finished" if s.get("finished") else "running")
        return
    run_shard(a.shard, a.once)


if __name__ == "__main__":
    main()
