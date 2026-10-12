"""প্রতি 9.6 মিনিটে স্ন্যাপশট নেয়। একটি GitHub রান ~5 ঘণ্টা 40 মিনিট চলে, তারপর পরের রান দায়িত্ব নেয়।
Keeps a fixed 9.6-minute grid that starts at the first snapshot, so every run lines up with the same schedule.
"""
import json
import math
import subprocess
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
from collector import DATA, ROOT, save_snapshot, take_snapshot, upload_to_drive

TZ = ZoneInfo(config.TIMEZONE)
STATE = DATA / "state.json"


def now():
    return datetime.now(TZ)


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    s = {"start": now().replace(microsecond=0).isoformat(), "last_slot": -1, "finished": False}
    DATA.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1))
    return s


def save_state(s):
    STATE.write_text(json.dumps(s, indent=1))


def git_push(message):
    paths = [p for p in ("data", "docs", "snapshots") if (ROOT / p).exists()]
    cmds = [["git", "add", "-A", *paths],
            ["git", "commit", "-q", "-m", message],
            ["git", "pull", "-q", "--rebase"],
            ["git", "push", "-q"]]
    for c in cmds:
        r = subprocess.run(c, cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0 and c[1] not in ("commit",):
            print(f"git {c[1]} problem: {r.stderr.strip()[:300]}")


def run_analysis():
    r = subprocess.run([sys.executable, "analyze.py"], cwd=ROOT, capture_output=True, text=True)
    print(r.stdout.strip()[-300:] or r.stderr.strip()[-300:])


def backup_data_to_drive():
    """Copies the CSV data files to Drive (folder: data), replacing the older copy."""
    for name in ("snapshots.csv", "thana.csv", "daily_summary.csv"):
        f = DATA / name
        if f.exists():
            upload_to_drive(name, "", f.read_bytes(), "text/csv", overwrite=True, subfolder="data")


def main():
    state = load_state()
    start = datetime.fromisoformat(state["start"])
    end = start + timedelta(days=config.COLLECTION_DAYS)
    deadline = now() + timedelta(minutes=config.RUN_MINUTES)
    step = config.INTERVAL_SECONDS
    print(f"Study start {start:%d %b %Y %H:%M:%S}, ends {end:%d %b %Y %H:%M}. This run stops by {deadline:%H:%M}.")

    while True:
        t = now()
        if t >= end:
            state["finished"] = True
            save_state(state)
            run_analysis()
            git_push("30-day collection finished")
            print("30 days complete. Collection stopped.")
            return
        elapsed = (t - start).total_seconds()
        slot = max(state["last_slot"] + 1, math.ceil(elapsed / step - 1e-6))
        # catch a slot we are only slightly late for (e.g. right after a hand-over)
        prev = slot - 1
        if prev > state["last_slot"] and elapsed - prev * step < 120:
            slot = prev
        slot_time = start + timedelta(seconds=slot * step)
        if slot_time > deadline:
            print("Run time used up; handing over to the next run.")
            return
        wait = (slot_time - now()).total_seconds()
        if wait > 0:
            time.sleep(wait)

        for attempt in (1, 2):
            try:
                img, mode = take_snapshot()
                taken = now().replace(microsecond=0)
                score, quality, drive = save_snapshot(img, mode, taken, slot)
                print(f"[slot {slot}] {taken:%Y-%m-%d %H:%M:%S} score={score} quality={quality} drive={drive}")
                break
            except Exception as e:
                print(f"[slot {slot}] attempt {attempt} failed: {e}")
                time.sleep(20)
        state["last_slot"] = slot
        save_state(state)
        run_analysis()
        git_push(f"snapshot slot {slot}")
        if slot % 6 == 0:  # about once an hour
            backup_data_to_drive()


if __name__ == "__main__":
    main()
