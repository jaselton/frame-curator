"""One-time (and re-runnable) pairing with The Frame, plus a small starter set so you see it working.

Power the TV on, then:
    frame-setup                 # uses FRAME_TV_HOST from .env
    frame-setup 192.168.1.99    # or a specific TV

Wakes the TV (the 2019 Frame sleeps its control ports in Art Mode), identifies it, pairs (tap **Allow** on the TV once),
uploads a pool of famous public-domain works, shows the first, and turns on the TV's Art-Mode slideshow so they shuffle.
Art is fetched *before* pairing so the upload runs in one burst and the Frame doesn't doze off mid-way.
Env: ART_POOL (count, default 8), ART_ROTATE_MIN (slideshow minutes, default 30).
"""
import os
import pathlib
import re
import sys
import time

from dotenv import load_dotenv

load_dotenv()
from . import art  # noqa: E402

ENV = pathlib.Path(".env")


def main() -> None:
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__); return
    host = sys.argv[1].strip() if len(sys.argv) > 1 else os.getenv("FRAME_TV_HOST", "")
    if not host:
        sys.exit("No TV address: pass it as an argument or set FRAME_TV_HOST in .env")
    fr = art.FrameTV(host)

    info = fr.info()
    if not info:
        print(f"{host} not answering — sending Wake-on-LAN and waiting…")
        fr.wake(os.getenv("FRAME_TV_MAC") or None)
        time.sleep(7)
        info = fr.info()
    if not info:
        sys.exit(f"No Samsung TV at {host}:8001. Power the TV on and confirm its IP on your router.")
    print(f"Found: {info.get('name') or 'Samsung TV'} — model {info.get('model')} "
          f"(Frame={info.get('frame')}, power={info.get('power')}) at {host}")
    mac = info.get("mac")

    # 1) fetch all the art FIRST (no TV needed) — then pair+upload is one continuous burst that keeps the Frame awake
    n = int(os.getenv("ART_POOL", "8"))
    print(f"\nFetching {n} public-domain works (The Met / Cleveland)…")
    jpegs, titles, seen, tries = [], [], set(), 0
    while len(jpegs) < n and tries < n * 3:
        tries += 1
        try:
            m = art.fetch_artwork()
            key = (m["title"], m["artist"])
            if key in seen:
                continue
            seen.add(key)
            jpegs.append(art.prepare(m["image"], m))
            titles.append(f'{m["title"]} — {m["artist"]} ({m["source"]})')
            print(f"  {len(jpegs)}. {titles[-1]}")
        except Exception as e:
            print(f"  (skip: {type(e).__name__})")
    if not jpegs:
        sys.exit("No artwork could be fetched — check internet access.")

    # 2) wake + pair
    fr.wake(mac)
    time.sleep(4)
    print("\n**  ACCEPT the Allow prompt on the TV now (for 'HomeDisplayArt')  **")
    try:
        ok = fr.supported()
    except Exception as e:
        sys.exit(f"Could not reach the art service: {type(e).__name__}: {e}\n"
                 f"Tap Allow on the TV, keep it awake, and re-run. Also check Settings → General → External Device "
                 f"Manager → Device Connect Manager (host must not be set to Deny).")
    if not ok:
        sys.exit("Connected but Art Mode reports unsupported.")
    print(f"Paired. Token saved to {fr.token_file}")

    # 3) upload + show + slideshow (continuous, TV stays awake while transferring)
    fr.wake(mac)
    old = fr.my_uploads()
    print(f"\nUploading {len(jpegs)} works (a few seconds each)…")
    ids = fr.upload_pool(jpegs)
    print(f"Uploaded {len(ids)} works.")
    if ids:
        fr.select(ids[0])
        print(f"Now showing: {titles[0]}")
    mins = int(os.getenv("ART_ROTATE_MIN", "30"))
    print(f"Slideshow {'ON' if fr.enable_slideshow(mins) else 'could NOT be enabled'} — shuffling every {mins} min in Art Mode.")
    if old:
        fr.delete(old)
        print(f"Removed {len(old)} works from the previous gallery.")

    s = ENV.read_text()
    s = re.sub(r"^FRAME_TV_HOST=.*$", f"FRAME_TV_HOST={host}", s, flags=re.M) if "FRAME_TV_HOST=" in s else s + f"\nFRAME_TV_HOST={host}\n"
    ENV.write_text(s)
    print(f"\nDone. {host} will rotate these whenever it is in Art Mode. Next: `frame-curate harvest`, vet, `frame-fill`.")


if __name__ == "__main__":
    main()
