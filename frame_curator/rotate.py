"""Daily rotation for The Frame: at midnight show the next piece from the manifest, unless someone picked one by hand.

    frame_rotate                 # the scheduled run (launchd / cron at 00:00)
    frame_rotate --dry-run       # explain what it would do, touch nothing
    frame_rotate --now           # rotate immediately, ignoring any manual hold

Rules:
- The TV's own auto-rotation stays OFF; this script is the only thing that changes the picture, so one piece holds
  for a whole day.
- The order is a shuffle of everything in the manifest (data/frame_uploads.json), persisted in
  data/frame_rotation.json so it survives restarts and keeps going after new uploads (new ids are shuffled in).
- If the picture on the TV is not the one this script last set, somebody changed it with the remote. That piece
  stays for the rest of that day AND the next day: the first midnight after a manual change is skipped, the one
  after that rotates. A second manual change starts a new hold.
- The 2019 Frame closes its control ports in Art-Mode sleep; a Wake-on-LAN packet is sent first and the run is
  retried a few times before giving up (logged, nothing changed).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import random
import sys
import time

from dotenv import load_dotenv

load_dotenv()
from . import art  # noqa: E402

STATE = art.DATA_DIR / "frame_rotation.json" if hasattr(art, "DATA_DIR") else art.ROOT / "data" / "frame_rotation.json"


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"order": [], "pos": -1, "last_set": None, "manual_hold": None, "hold_skipped": False, "history": []}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(s, indent=1)); tmp.replace(STATE)


def log(msg: str) -> None:
    print(f"{dt.datetime.now().isoformat(timespec='seconds')}  {msg}", flush=True)


def sync_order(state: dict, ids: list[str]) -> None:
    """Keep the shuffled order in step with the manifest: drop deleted ids, shuffle new ones into the unplayed tail."""
    have = set(ids)
    order = [c for c in state["order"] if c in have]
    played = set(order[: state["pos"] + 1])
    new = [c for c in ids if c not in set(order)]
    random.shuffle(new)
    state["order"] = order + new
    state["pos"] = min(state["pos"], len(order) - 1)
    if not state["order"]:
        return
    # when the whole list has been shown, reshuffle for the next pass
    if state["pos"] >= len(state["order"]) - 1:
        random.shuffle(state["order"]); state["pos"] = -1
    state.setdefault("history", [])
    _ = played


PICTURE_STUDY = STATE.parent / "collections" / "picture_study.json"


def picture_study_piece(manifest: dict, today: str | None = None) -> str | None:
    """content_id scheduled for today by collections/picture_study.json, or None outside a term."""
    try:
        ps = json.loads(PICTURE_STUDY.read_text())
    except Exception:
        return None
    today = today or dt.date.today().isoformat()
    for p in ps.get("pieces", []):
        if p.get("from", "9999") <= today <= p.get("to", "0000"):
            cid = p.get("content_id")
            if cid in manifest:
                return cid
            # content ids change when the TV is cleared; fall back to the ref
            for k, v in manifest.items():
                if v.get("ref") == p.get("ref"):
                    return k
    return None


def current_on_tv(fr) -> str | None:
    try:
        cur = fr._call(lambda a: a.get_current(), tries=2) or {}
        return cur.get("content_id")
    except Exception:
        return None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--now", action="store_true", help="rotate immediately, ignoring a manual hold")
    p.add_argument("--date", help="pretend today is YYYY-MM-DD (for testing the picture-study schedule)")
    p.add_argument("host", nargs="?", default=os.getenv("FRAME_TV_HOST", ""))
    a = p.parse_args()
    host, mac = a.host.strip(), (os.getenv("FRAME_TV_MAC") or None)
    if not host:
        sys.exit("No TV address: set FRAME_TV_HOST")

    manifest = art.manifest_load()
    ids = list(manifest)
    if not ids:
        log("manifest is empty, nothing to rotate"); return
    state = load_state()
    sync_order(state, ids)

    fr = art.FrameTV(host)
    cur = None
    for attempt in range(4):
        fr.wake(mac); time.sleep(6 if attempt == 0 else 15)
        if art.FrameTV.reachable(host):
            cur = current_on_tv(fr)
            if cur:
                break
    if not cur:
        log(f"{host} not reachable after wake attempts; leaving the picture as it is"); save_state(state); return

    title = lambda c: f"{manifest.get(c, {}).get('artist', '?')} — {manifest.get(c, {}).get('title', c)}"
    log(f"on the TV now: {cur} ({title(cur)}); last set by us: {state.get('last_set')}")

    # Picture study: during a term the scheduled piece holds the wall for its whole window (weekends included) and
    # outranks both the daily rotation and the two-day manual hold. Nothing else advances while it is up.
    ps = picture_study_piece(manifest, a.date)
    if ps and not a.now:
        if cur == ps:
            log(f"picture study: {title(ps)} stays up"); save_state(state); return
        log(f"picture study: showing {title(ps)}" + ("  DRY RUN" if a.dry_run else ""))
        if a.dry_run:
            return
        try:
            fr.select(ps)
        except Exception as e:
            log(f"select failed: {type(e).__name__}: {e}"); save_state(state); return
        state["last_set"] = ps; state["manual_hold"], state["hold_skipped"] = None, False
        state.setdefault("history", []).append({"at": dt.datetime.now().isoformat(timespec="seconds"), "content_id": ps, "reason": "picture_study"})
        save_state(state); log("done"); return

    manual = cur != state.get("last_set") and cur in manifest
    if manual and not a.now:
        if cur == state.get("manual_hold") and state.get("hold_skipped"):
            log("manual pick has had its extra day; resuming the schedule")
            state["manual_hold"], state["hold_skipped"] = None, False
        else:
            state["manual_hold"], state["hold_skipped"] = cur, True
            log("picked by hand since the last rotation: keeping it today and tomorrow (skipping this rotation)")
            if not a.dry_run:
                save_state(state)
            return
    elif cur not in manifest and not a.now:
        log("the TV is showing something outside the manifest (built-in art?); rotating onto the gallery")

    state["pos"] += 1
    nxt = state["order"][state["pos"]]
    if nxt == cur and len(state["order"]) > 1:
        state["pos"] += 1
        if state["pos"] >= len(state["order"]):
            random.shuffle(state["order"]); state["pos"] = 0
        nxt = state["order"][state["pos"]]
    log(f"next: {nxt} ({title(nxt)})  [{state['pos'] + 1}/{len(state['order'])}]" + ("  DRY RUN" if a.dry_run else ""))
    if a.dry_run:
        return
    try:
        fr.select(nxt)
    except Exception as e:
        log(f"select failed: {type(e).__name__}: {e}"); state["pos"] -= 1; save_state(state); return
    state["last_set"] = nxt
    state["manual_hold"], state["hold_skipped"] = None, False
    state.setdefault("history", []).append({"at": dt.datetime.now().isoformat(timespec="seconds"), "content_id": nxt})
    state["history"] = state["history"][-400:]
    save_state(state)
    log("done")


if __name__ == "__main__":
    main()
