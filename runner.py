"""সময়সূচি অনুযায়ী স্ন্যাপশট নেয়: রাত 1টা–5টা প্রতি ঘণ্টায়, বাকি সময় প্রতি 15 মিনিটে, 30 দিন।
একটি GitHub রান ~5 ঘণ্টা 40 মিনিট চলে, তারপর পরের রান দায়িত্ব নেয়।

  python runner.py --plan            # show area size, tiles, time per snapshot and storage for 30 days
  python runner.py --init            # once per GitHub run: creates/checks data/state.json, prints shard list
  python runner.py --shard 0         # one worker (GitHub runs one per shard)
  python runner.py --shard 0 --once  # take one snapshot right now (test on your PC)
"""
import argparse
import json
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
import collector
from collector import DATA, ROOT

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


def init_state():
    """Creates data/state.json (or archives an older study version). Returns the state."""
    DATA.mkdir(exist_ok=True)
    if STATE.exists():
        s = json.loads(STATE.read_text())
        if s.get("version", 1) == config.STUDY_VERSION:
            return s
        archive_old_study(s.get("version", 1))
    start = config.next_slot(now() + timedelta(minutes=4))  # leave time for the workers to install
    s = {"start": start.isoformat(), "end": (start + timedelta(days=config.COLLECTION_DAYS)).isoformat(),
         "finished": False, "version": config.STUDY_VERSION, "zoom": config.ZOOM, "shards": config.SHARDS,
         "bounds": config.BOUNDS, "slots_per_day": config.SLOTS_PER_DAY}
    STATE.write_text(json.dumps(s, indent=1))
    print(f"New study: first snapshot {start:%d %b %Y %H:%M} Dhaka time, {config.COLLECTION_DAYS} days, "
          f"{config.SLOTS_PER_DAY} snapshots per day.")
    return s


def progress_file(shard):
    return collector.SHARD_DIR / f"progress_{shard:02d}.json"


def load_progress(shard):
    f = progress_file(shard)
    return json.loads(f.read_text()) if f.exists() else {"last_slot": "", "image_bytes": 0, "snapshots": 0}


def git_push(message, paths):
    """Commit and push; several workers may push at the same time, so retry with a short random wait."""
    paths = [p for p in paths if (ROOT / p).exists()]
    if not paths:
        return
    subprocess.run(["git", "add", "-A", *paths], cwd=ROOT)
    if subprocess.run(["git", "commit", "-q", "-m", message], cwd=ROOT, capture_output=True).returncode != 0:
        return
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


def backup_data_to_drive(drive):
    """Copies the combined CSV files to Drive (folder: data), replacing the older copy."""
    if not drive.enabled:
        return
    for name in ("snapshots.csv", "thana.csv", "conditions.csv", "daily_summary.csv"):
        f = DATA / name
        if f.exists():
            try:
                drive.small_file(name, f.read_bytes(), "text/csv", "data")
            except Exception as e:
                print(f"CSV backup {name} failed: {e}")


def plan():
    ns, ew, km2 = config.area_km2()
    mp = config.VIEW_W * config.VIEW_H / 1e6
    tiles = config.TILES_X * config.TILES_Y
    per_runner = max(len(collector.shard_tiles(s)) for s in range(config.SHARDS))
    per_mp = {"webp": 0.146, "jpg": 0.217, "png": 0.6}.get(config.IMAGE_FORMAT.lower(), 0.2)  # MB per megapixel, upper estimate
    img_mb = mp * per_mp
    n = config.TOTAL_SLOTS
    print(f"Area: {ns:.1f} km (N-S) x {ew:.1f} km (E-W) = {km2:.0f} km²")
    print(f"Zoom {config.ZOOM}: {config.metres_per_px(config.CENTER_LAT):.2f} m per pixel")
    print(f"Image: {config.VIEW_W:,} x {config.VIEW_H:,} px = {mp:.0f} megapixels (+ footer)")
    print(f"Tiles: {config.TILES_X} x {config.TILES_Y} = {tiles}; workers (shards): {config.SHARDS}; "
          f"max {per_runner} tiles per worker ≈ {per_runner * 12 / 60:.1f} min per snapshot")
    print(f"Schedule: {config.SLOTS_PER_DAY} snapshots per day, {n:,} in {config.COLLECTION_DAYS} days")
    print(f"Storage ({config.IMAGE_FORMAT}, upper estimate): ≈ {img_mb:.0f} MB per snapshot → "
          f"{img_mb * config.SLOTS_PER_DAY / 1000:.1f} GB per day → {img_mb * n / 1000:,.0f} GB in 30 days")
    print(f"Zones in this area: {', '.join(collector.ZONES_PRESENT)}")
    if per_runner * 12 > config.DAY_INTERVAL_MIN * 60 * 0.8:
        print("⚠ One snapshot may not finish within 15 minutes: lower TILES_PER_RUNNER in config.py.")


def run_shard(shard, once=False):
    state = json.loads(STATE.read_text()) if STATE.exists() else init_state()
    start = datetime.fromisoformat(state["start"])
    end = datetime.fromisoformat(state.get("end") or (start + timedelta(days=config.COLLECTION_DAYS)).isoformat())
    deadline = now() + timedelta(minutes=config.RUN_MINUTES)
    prog = load_progress(shard)
    uploader = collector.Uploader(shard)
    my_paths = [f"data/shards/shard_{shard:02d}.csv", f"data/shards/shard_{shard:02d}_thana.csv",
                f"data/shards/progress_{shard:02d}.json", f"data/shards/uploads_{shard:02d}.csv"]
    if shard == 0:
        my_paths += ["data", "docs"]
    print(f"Worker {shard}/{config.SHARDS}: {len(collector.shard_tiles(shard))} tiles per snapshot. "
          f"Study {start:%d %b %H:%M} → {end:%d %b %H:%M}. This run stops by {deadline:%H:%M}. "
          f"Drive upload: {'on' if uploader.drive.enabled else 'OFF (DRIVE_WEBAPP_URL missing)'}")

    while True:
        t = now()
        if once:
            target = t.replace(microsecond=0)
        else:
            if t >= end:
                break
            p = config.prev_slot(t)
            if p >= start and config.slot_id(p) > prog["last_slot"] and (t - p).total_seconds() < config.LATE_GRACE_SECONDS:
                target = p  # only slightly late: take it now
            else:
                target = config.next_slot(t)
                if target < start:
                    target = start
            if target >= end:
                wait = (end - now()).total_seconds()
                if wait > 0 and end <= deadline:
                    time.sleep(wait)
                    continue
                break
            if target > deadline:
                print("Run time used up; handing over to the next run.")
                break
            wait = (target - now()).total_seconds()
            if wait > 0:
                time.sleep(wait)

        t0 = time.time()
        interval = (config.next_slot(target) - target).total_seconds()
        try:
            r = collector.run_cycle(target, shard, uploader)
            prog["image_bytes"] += r["bytes"]
            prog["snapshots"] += 1
            took = time.time() - t0
            print(f"[{config.slot_id(target)}] worker {shard}: tiles {r['tiles_ok']}/{r['tiles']} score={r['score']} "
                  f"{r['captured_at']:%H:%M:%S}-{r['capture_end']:%H:%M:%S} took={took:.0f}s "
                  f"image={r['bytes'] / 1e6:.0f}MB total={prog['image_bytes'] / 1e9:.1f}GB")
            if took > interval * 0.8:
                print("⚠ This snapshot used more than 80% of its interval. Lower TILES_PER_RUNNER in config.py.")
        except Exception as e:
            print(f"[{config.slot_id(target)}] failed: {e}")
        prog["last_slot"] = config.slot_id(target)
        progress_file(shard).write_text(json.dumps(prog))
        if shard == 0:
            run_analysis()
            if target.minute == 0:  # once an hour
                backup_data_to_drive(uploader.drive)
        git_push(f"{config.slot_id(target)} worker {shard}", my_paths)
        if once:
            break

    print("Waiting for uploads to finish…")
    uploader.drain(timeout=20 * 60)
    git_push(f"uploads log worker {shard}", my_paths)
    if not once and now() >= end and shard == 0:
        state["finished"] = True
        STATE.write_text(json.dumps(state, indent=1))
        run_analysis()
        backup_data_to_drive(uploader.drive)
        git_push("30-day collection finished", my_paths)
        print("30 days complete. Collection stopped.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    if a.plan:
        plan()
        return
    if a.init:
        s = init_state()
        print(json.dumps(list(range(config.SHARDS))))
        print("finished" if s.get("finished") else "running")
        return
    run_shard(a.shard, a.once)


if __name__ == "__main__":
    main()
