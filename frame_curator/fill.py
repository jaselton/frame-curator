"""Upload the hand-vetted, family-safe gallery to The Frame and keep a manifest of what is on it.

    frame-fill                  # clear the TV, upload every approved piece in <data>/frame_gallery.json
    frame-fill --keep           # add the approved pieces that are not on the TV yet, clear nothing
    frame-fill --limit 60       # stop after N uploads
    frame-fill 192.168.1.99     # a specific TV (default: FRAME_TV_HOST from .env)

Only entries in frame_gallery.json (written by `frame-curate pick ...`) are ever uploaded; there is no random harvest.
Every upload and every clear is recorded in frame_uploads.json: content_id -> {artist, title, date, source, image_url,
sha1, ref, uploaded_at}, so any piece can be traced back and removed one at a time. The TV must be powered on; a
Wake-on-LAN packet is sent first (set FRAME_TV_MAC). Storage ceiling on a 2019 65" Frame is ~227 uploads.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

from dotenv import load_dotenv

load_dotenv()
from . import art  # noqa: E402

GALLERY_FILE = art.DATA_DIR / "frame_gallery.json"
CAND_DIR = art.DATA_DIR / "frame_candidates"


def load_image(c: httpx.Client, it: dict) -> bytes | None:
    """The original museum file saved at curation time; falls back to re-downloading the recorded URL."""
    fn = CAND_DIR / it.get("file", "")
    if it.get("file") and fn.exists():
        return fn.read_bytes()
    try:
        r = c.get(it["image_url"])
        if r.status_code == 200 and r.content.startswith((b"\xff\xd8", b"\x89PNG")):
            return r.content
    except Exception:
        pass
    return None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host", nargs="?", default=os.getenv("FRAME_TV_HOST", ""))
    p.add_argument("--keep", action="store_true", help="do not clear the TV first; skip pieces already in the manifest")
    p.add_argument("--limit", type=int, default=int(os.getenv("FRAME_TARGET", "0")) or None)
    a = p.parse_args()
    host, mac = a.host.strip(), (os.getenv("FRAME_TV_MAC") or None)
    if not host:
        sys.exit("No TV address: pass it as an argument or set FRAME_TV_HOST in .env")

    try:
        gallery = json.loads(GALLERY_FILE.read_text())["items"]
    except Exception:
        sys.exit(f"{GALLERY_FILE} missing or unreadable — run `frame-curate harvest` and `frame-curate pick` first.")
    gallery = [it for it in gallery if it.get("status") == "approved"]
    if not gallery:
        sys.exit("gallery has no approved pieces — nothing to upload.")

    fr = art.FrameTV(host)
    fr.wake(mac); time.sleep(5)
    if not art.FrameTV.reachable(host):
        fr.wake(mac); time.sleep(8)
    if not art.FrameTV.reachable(host):
        sys.exit(f"{host} not reachable — power the TV on.")
    print(f"TV: {fr.info()}", flush=True)

    manifest = art.manifest_load()
    if a.keep:
        have = {v.get("sha1") for v in manifest.values()}
        todo = [it for it in gallery if it.get("sha1") not in have]
        print(f"--keep: {len(gallery) - len(todo)} already on the TV, {len(todo)} to add", flush=True)
    else:
        old = fr.my_uploads()
        print(f"clearing {len(old)} existing works...", flush=True)
        for j in range(0, len(old), 20):
            fr.delete(old[j:j + 20]); time.sleep(0.3)
        art.manifest_forget(list(art.manifest_load()))   # the TV is empty now; the manifest must say so too
        todo = gallery
    if a.limit:
        todo = todo[:a.limit]

    c = httpx.Client(timeout=90, headers=art.UA, follow_redirects=True)
    uploaded, failed = [], []
    print(f"uploading {len(todo)} vetted pieces (white mat + brass plaque)...", flush=True)
    for i, it in enumerate(todo, 1):
        img = load_image(c, it)
        if not img:
            failed.append(it); print(f"  skip (no image): {it['artist']} — {it['title'][:40]}", flush=True); continue
        try:
            jpg = art.prepare(img, it)
        except Exception as e:
            failed.append(it); print(f"  skip (prepare {type(e).__name__}): {it['title'][:40]}", flush=True); continue
        if i % 8 == 1:
            fr.wake(mac)
        try:
            cid = fr.upload_one(jpg)
        except Exception as e:
            print(f"UPLOAD REFUSED at {len(uploaded)} — {type(e).__name__}: {e} (storage ceiling or TV asleep)", flush=True)
            failed.append(it); break
        art.manifest_record(cid, it, img)
        uploaded.append(cid)
        print(f"  {len(uploaded):3d}/{len(todo)}  {cid}  {it['artist'][:26]:26s} — {it['title'][:44]}", flush=True)

    print(f"\nDONE: {len(uploaded)} uploaded, {len(failed)} skipped/failed. Manifest: {art.MANIFEST} "
          f"({len(art.manifest_load())} entries).", flush=True)
    if uploaded:
        try:
            fr.select(uploaded[0])
            print(f"showing {uploaded[0]}", flush=True)
        except Exception as e:
            print(f"could not select piece 0: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
