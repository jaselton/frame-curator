"""Hand-vetted, family-safe gallery for The Frame — the curation side.

    frame-curate harvest [--target 160]     # pull gated candidates (Met + Cleveland) + numbered contact sheets
    frame-curate named lists/americana.json --theme americana   # an explicit list of works (met:/cma:/commons: refs)
    frame-curate sheets                     # rebuild the contact sheets from the candidate folder
    frame-curate trim [ids]                 # crop museum "on the easel" photos to the canvas (clips, colour bars)
    frame-curate pick 1-12,15,20            # approve candidates into <data>/frame_gallery.json
    frame-curate drop 3,4                   # reject (also removes from the gallery)
    frame-curate status                     # counts, watch-list hits, what's approved

Nothing here touches the TV. Every candidate is saved at original resolution under <data>/frame_candidates/ and laid
out on numbered contact sheets (<data>/frame_sheets/) so a human eyeballs each one before it is approved.
The upload side is `frame-fill`, which only ever pushes approved gallery entries and writes the manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import pathlib
import random
import sys
import time

import httpx

from dotenv import load_dotenv

load_dotenv()
from . import art  # noqa: E402

CAND_DIR = art.DATA_DIR / "frame_candidates"
SHEET_DIR = art.DATA_DIR / "frame_sheets"
CAND_FILE = art.DATA_DIR / "frame_candidates.json"
GALLERY_FILE = art.DATA_DIR / "frame_gallery.json"
SHEET_COLS, SHEET_ROWS, CELL_W, CELL_H, LABEL_H = 5, 3, 500, 340, 44


# ---------- state ----------
def load_cands() -> dict:
    try:
        return json.loads(CAND_FILE.read_text())
    except Exception:
        return {"items": []}


def save_cands(state: dict) -> None:
    CAND_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CAND_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    tmp.replace(CAND_FILE)


def write_gallery(state: dict) -> int:
    import datetime as dt
    approved = [it for it in state["items"] if it.get("status") == "approved"]
    GALLERY_FILE.parent.mkdir(parents=True, exist_ok=True)
    GALLERY_FILE.write_text(json.dumps({
        "updated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": "Hand-vetted family-safe gallery. Only entries in this file are ever uploaded (frame-fill).",
        "items": approved}, indent=1, ensure_ascii=False))
    return len(approved)


def parse_ids(spec: str) -> list[int]:
    out = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


# ---------- sources (search → gated metadata) ----------
def met_search(c: httpx.Client, term: str, memo: dict, limit: int = 30) -> list[dict]:
    try:
        r = c.get(art.MET_SEARCH, params={"q": term, "hasImages": "true", "isPublicDomain": "true",
                                          "departmentId": art.MET_DEPT_EUROPEAN_PAINTINGS, "limit": limit})
        ids = (r.json() or {}).get("objectIDs") or []
    except Exception:
        return []
    out = []
    for oid in ids:
        if oid in memo:
            if memo[oid]:
                out.append(memo[oid])
            continue
        try:
            meta = art.met_object_meta(c.get(art.MET_OBJECT.format(oid)).json())
        except Exception:
            meta = None
        memo[oid] = meta
        if meta:
            out.append(meta)
        time.sleep(0.25)     # the Met answers bursts with 403
    return out


def cleveland_search(c: httpx.Client, term: str, limit: int = 40) -> list[dict]:
    out = []
    for dept in art.CLEVELAND_DEPTS:
        try:
            r = c.get("https://openaccess-api.clevelandart.org/api/artworks/",
                      params={"cc0": "1", "has_image": "1", "q": term, "type": "Painting", "department": dept, "limit": limit})
            out += [m for m in (art.cleveland_item_meta(d) for d in (r.json() or {}).get("data", [])) if m]
        except Exception:
            continue
    return out


# ---------- harvest ----------
def harvest(target: int, per_term: int) -> None:
    from PIL import Image
    state = load_cands()
    items = state["items"]
    seen_ref = {it["ref"] for it in items}
    seen_key = {(it["artist"].strip().lower(), it["title"].strip().lower()) for it in items}
    seen_sha = {it["sha1"] for it in items}
    n = max([it["n"] for it in items], default=0)
    CAND_DIR.mkdir(parents=True, exist_ok=True)
    c = httpx.Client(timeout=90, headers=art.UA, follow_redirects=True)
    terms = list(art.TERMS)
    random.Random(7).shuffle(terms)
    memo: dict = {}
    added = 0
    print(f"harvesting up to {target} new family-safe candidates from the Met + Cleveland ({len(terms)} terms)...", flush=True)
    for term in terms:
        if added >= target:
            break
        metas = met_search(c, term, memo) + cleveland_search(c, term)
        random.shuffle(metas)
        got = 0
        for meta in metas:
            if added >= target or got >= per_term:
                break
            key = (meta["artist"].strip().lower(), meta["title"].strip().lower())
            if meta["ref"] in seen_ref or key in seen_key:
                continue
            try:
                r = _get_retry(c, meta["image_url"])
                img = r.content
                if r.status_code != 200 or not img.startswith((b"\xff\xd8", b"\x89PNG")):
                    continue
                if not art._landscape(img):
                    continue
                sha = hashlib.sha1(img).hexdigest()
                if sha in seen_sha:
                    continue
                w, h = Image.open(io.BytesIO(img)).size
                if w < 1400:          # too soft for a 65" panel
                    continue
            except Exception:
                continue
            n += 1
            fn = CAND_DIR / f"{n:03d}.jpg"
            if img.startswith(b"\x89PNG"):
                Image.open(io.BytesIO(img)).convert("RGB").save(fn, "JPEG", quality=95)
            else:
                fn.write_bytes(img)
            it = dict(meta); it.update({"n": n, "sha1": sha, "w": w, "h": h, "file": fn.name, "status": "pending",
                                        "term": term, "watch": art.watch_hits(meta["title"], *meta.get("tags", []))})
            items.append(it); seen_ref.add(meta["ref"]); seen_key.add(key); seen_sha.add(sha)
            added += 1; got += 1
            print(f"  {n:3d}  {meta['source'][:9]:9s}  {meta['artist'][:28]:28s}  {meta['title'][:48]}", flush=True)
            save_cands(state)
    print(f"\n{added} new candidates ({len(items)} total). Building contact sheets...", flush=True)
    sheets()


# ---------- contact sheets ----------
def sheets() -> None:
    from PIL import Image, ImageDraw
    state = load_cands()
    items = [it for it in state["items"] if it.get("status") != "rejected"]
    SHEET_DIR.mkdir(parents=True, exist_ok=True)
    for old in SHEET_DIR.glob("sheet-*.jpg"):
        old.unlink()
    per = SHEET_COLS * SHEET_ROWS
    f_num, f_txt = art._font("Georgia Bold.ttf", 34), art._font("Georgia.ttf", 18)
    for s in range(0, len(items), per):
        batch = items[s:s + per]
        W, H = SHEET_COLS * CELL_W, SHEET_ROWS * (CELL_H + LABEL_H)
        sheet = Image.new("RGB", (W, H), (28, 28, 30))
        d = ImageDraw.Draw(sheet)
        for i, it in enumerate(batch):
            col, row = i % SHEET_COLS, i // SHEET_COLS
            x0, y0 = col * CELL_W, row * (CELL_H + LABEL_H)
            try:
                im = Image.open(CAND_DIR / it["file"]).convert("RGB")
                im.thumbnail((CELL_W - 12, CELL_H - 12))
                sheet.paste(im, (x0 + (CELL_W - im.width) // 2, y0 + (CELL_H - im.height) // 2))
            except Exception:
                d.text((x0 + 20, y0 + 20), "missing file", font=f_txt, fill=(255, 80, 80))
            tag = f"{it['n']}" + ("  ✓" if it.get("status") == "approved" else "")
            d.rectangle([x0 + 6, y0 + 6, x0 + 6 + 30 + 20 * len(tag), y0 + 50], fill=(0, 0, 0))
            d.text((x0 + 14, y0 + 8), tag, font=f_num, fill=(255, 220, 90))
            watch = (" [" + ",".join(it.get("watch") or []) + "]") if it.get("watch") else ""
            label = f"{it['artist'][:30]} — {it['title'][:40]} ({it['date'][:12]}){watch}"
            d.text((x0 + 8, y0 + CELL_H + 10), label, font=f_txt, fill=(235, 235, 235))
        out = SHEET_DIR / f"sheet-{s // per + 1:02d}.jpg"
        sheet.save(out, "JPEG", quality=85)
        print(f"  {out.name}: #{batch[0]['n']}–#{batch[-1]['n']}")


# ---------- decisions ----------
def set_status(ids: list[int], status: str) -> None:
    state = load_cands()
    by_n = {it["n"]: it for it in state["items"]}
    missing = [i for i in ids if i not in by_n]
    if missing:
        sys.exit(f"unknown candidate numbers: {missing}")
    for i in ids:
        by_n[i]["status"] = status
    save_cands(state)
    print(f"{status}: {len(ids)}  → gallery now has {write_gallery(state)} approved pieces")


def status() -> None:
    state = load_cands()
    items = state["items"]
    from collections import Counter
    print(Counter(it.get("status", "pending") for it in items))
    print("by source:", Counter(it["source"] for it in items))
    watch = [it for it in items if it.get("watch") and it.get("status") != "rejected"]
    if watch:
        print(f"\nwatch-list hits ({len(watch)}), look at these at full size:")
        for it in watch:
            print(f"  {it['n']:3d} [{it.get('status','pending'):8s}] {it['artist'][:26]:26s} {it['title'][:50]:50s} {it['watch']}")
    print(f"\ngallery file: {GALLERY_FILE.name} → {write_gallery(state)} approved")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("harvest"); h.add_argument("--target", type=int, default=160); h.add_argument("--per-term", type=int, default=5)
    sub.add_parser("sheets")
    pk = sub.add_parser("pick"); pk.add_argument("ids")
    dr = sub.add_parser("drop"); dr.add_argument("ids")
    sub.add_parser("status")
    nm = sub.add_parser("named"); nm.add_argument("list_path"); nm.add_argument("--theme", default="named")
    tr = sub.add_parser("trim"); tr.add_argument("ids", nargs="?", default=None, help="candidate numbers; default = all non-rejected"); tr.add_argument("-v", action="store_true")
    a = p.parse_args()
    if a.cmd == "harvest":
        harvest(a.target, a.per_term)
    elif a.cmd == "sheets":
        sheets()
    elif a.cmd == "pick":
        set_status(parse_ids(a.ids), "approved")
    elif a.cmd == "drop":
        set_status(parse_ids(a.ids), "rejected")
    elif a.cmd == "named":
        named(a.list_path, a.theme)
    elif a.cmd == "trim":
        trim(parse_ids(a.ids) if a.ids else None, verbose=a.v)
    else:
        status()




# ---------- trim: museum "on the easel" photos carry black borders, clips and colour-calibration strips ----------
def trim_canvas(path: pathlib.Path, max_cut: float = 0.16, inset: float = 0.025) -> tuple[bool, str]:
    """Crop a candidate to its canvas: find the largest contiguous block of non-dark rows/columns (the colour bar and
    clips sit in the dark surround, separated from the canvas), then inset a touch to lose the canvas edge/clips.
    Refuses to cut more than max_cut per side, and leaves genuinely dark paintings alone. Keeps NNN.orig.jpg."""
    from PIL import Image
    orig = path.with_suffix(".orig.jpg")
    src = orig if orig.exists() else path        # always re-derive from the untouched original
    im = Image.open(src).convert("RGB")
    w, h = im.size
    g = im.convert("L").resize((max(1, w // 4), max(1, h // 4)))
    px = g.load(); gw, gh = g.size
    if sum(px[x, y] for x in range(0, gw, 7) for y in range(0, gh, 7)) / ((gw // 7 + 1) * (gh // 7 + 1)) < 45:
        return False, "dark painting, left alone"
    cols = [sum(1 for y in range(0, gh, 2) if px[x, y] > 38) / (gh / 2) for x in range(gw)]
    rows = [sum(1 for x in range(0, gw, 2) if px[x, y] > 38) / (gw / 2) for y in range(gh)]

    def block(v, thr=0.55):
        best, cur = (0, -1), None
        for i, val in enumerate(v + [0]):
            if val > thr:
                cur = i if cur is None else cur
            elif cur is not None:
                if i - cur > best[1] - best[0]:
                    best = (cur, i)
                cur = None
        return best
    x0, x1 = block(cols); y0, y1 = block(rows)
    if x1 <= x0 or y1 <= y0:
        return False, "no canvas block found"
    X0, X1, Y0, Y1 = x0 * 4, x1 * 4, y0 * 4, y1 * 4
    how = "block"
    if X0 > w * max_cut or (w - X1) > w * max_cut or Y0 > h * max_cut or (h - Y1) > h * max_cut:
        # The painting itself is dark along an edge, so the bright-block test ate into it. Fall back to an edge-in
        # scan: peel rows/cols that are almost entirely black (the photo surround), then look for a thin bright
        # strip separated from the canvas by a dark gap near an edge (a colour bar) and cut at the gap.
        def peel(v, lo, hi, step):
            i = lo
            while lo <= i < hi and v[i] < 0.04:     # only a truly black surround, never a dark painting edge
                i += step
            return i
        gx0, gx1 = peel(cols, 0, gw, 1), peel(cols, gw - 1, -1, -1) + 1
        gy0, gy1 = peel(rows, 0, gh, 1), peel(rows, gh - 1, -1, -1) + 1

        def gap_cut(v, a, b):
            span = b - a
            lim = int(span * 0.12)
            lo_i = min(range(a + 2, a + lim), key=lambda i: v[i], default=None) if lim > 4 else None
            hi_i = min(range(b - lim, b - 2), key=lambda i: v[i], default=None) if lim > 4 else None
            if lo_i is not None and v[lo_i] < 0.3:
                a = lo_i + 1
            if hi_i is not None and v[hi_i] < 0.3:
                b = hi_i
            return a, b
        gx0, gx1 = gap_cut(cols, gx0, gx1)
        gy0, gy1 = gap_cut(rows, gy0, gy1)
        X0, X1, Y0, Y1 = gx0 * 4, gx1 * 4, gy0 * 4, gy1 * 4
        how = "edge"
        if X0 > w * max_cut or (w - X1) > w * max_cut or Y0 > h * max_cut or (h - Y1) > h * max_cut:
            return False, f"would cut too much ({X0},{w-X1},{Y0},{h-Y1})"
        if (X0 + (w - X1) + Y0 + (h - Y1)) < 0.004 * (w + h):
            return False, "nothing to trim"      # no surround at all: a tight museum crop, leave it
    # No photographic surround at all (a tight museum crop): leave the file exactly as delivered. The inset exists to
    # lose easel clips and canvas edges, which only tight-cropped files never have.
    if (X0 + (w - X1) + Y0 + (h - Y1)) < 0.006 * (w + h):
        if orig.exists():
            orig.replace(path)          # undo an earlier inset-only trim
        return False, "nothing to trim"
    dx, dy = int((X1 - X0) * inset), int((Y1 - Y0) * inset)
    box = (X0 + dx, Y0 + dy, X1 - dx, Y1 - dy)
    if not orig.exists():
        path.replace(orig)
    Image.open(orig).convert("RGB").crop(box).save(path, "JPEG", quality=95)
    return True, f"{w}x{h} -> {box[2]-box[0]}x{box[3]-box[1]} ({how})"


def trim(ids: list[int] | None, verbose: bool = False) -> None:
    state = load_cands()
    items = [it for it in state["items"] if (ids is None or it["n"] in ids) and it.get("status") != "rejected"]
    done = 0
    for it in items:
        ok, msg = trim_canvas(CAND_DIR / it["file"])
        if ok:
            done += 1
            it["trimmed"] = msg
            print(f"  {it['n']:3d} trimmed {msg}  {it['title'][:40]}")
        else:
            if msg == "nothing to trim":
                it.pop("trimmed", None)
            if verbose:
                print(f"  {it['n']:3d} skipped: {msg}  {it['title'][:40]}")
    save_cands(state)
    print(f"trimmed {done} of {len(items)}; rebuilding sheets")
    sheets()


# ---------- named lists: explicit, owner-chosen works (e.g. Americana) ----------
def _commons_info(c: httpx.Client, title: str) -> dict | None:
    r = c.get("https://commons.wikimedia.org/w/api.php", params={"action": "query", "titles": title, "prop": "imageinfo",
                                                                  "iiprop": "url|size|extmetadata", "format": "json"})
    page = next(iter(r.json()["query"]["pages"].values()))
    ii = (page.get("imageinfo") or [None])[0]
    if not ii:
        return None
    em = ii.get("extmetadata") or {}
    lic = (em.get("LicenseShortName") or {}).get("value", "")
    if not any(k in lic.lower() for k in ("public domain", "cc0", "pd")):
        return None
    # upload.wikimedia.org throttles scripted downloads of originals (HTTP 429); the thumbnail route serves the same
    # file re-encoded at up to ~5500 px without the throttle, which is plenty for a 3840-px panel.
    w = int(ii.get("width") or 0)
    name = title.split(":", 1)[1] if title.lower().startswith("file:") else title
    url = ii["url"]
    if w > 2200:
        url = "https://commons.wikimedia.org/wiki/Special:FilePath/" + name.replace(" ", "_") + f"?width={min(w - 1, 5500)}"
    return {"url": url, "w": w, "h": ii.get("height"), "license": lic}


def resolve_named(c: httpx.Client, entry: dict) -> dict | None:
    """entry: {"ref": "met:11417" | "cma:141639" | "commons:File:...", optional artist/title/date overrides}.
    Returns our metadata dict (CORE subject gate only, no safe-tag requirement) or None."""
    ref = entry["ref"]
    kind, _, key = ref.partition(":")
    meta = None
    if kind == "met":
        o = c.get(art.MET_OBJECT.format(key)).json()
        if not (o.get("isPublicDomain") and o.get("primaryImage")):
            return None
        tags = [t.get("term", "") for t in (o.get("tags") or [])]
        meta = {"source": "The Met", "title": o.get("title") or "Untitled", "artist": o.get("artistDisplayName") or "Unknown",
                "date": o.get("objectDate") or "", "image_url": o["primaryImage"], "tags": tags, "ref": f"met:{o.get('objectID')}"}
    elif kind == "cma":
        d = c.get(f"https://openaccess-api.clevelandart.org/api/artworks/{key}").json().get("data") or {}
        if not (d.get("images") or {}).get("print") or not d.get("share_license_status", "").upper().startswith("CC0"):
            return None
        creators = d.get("creators") or []
        who = creators[0].get("description", "") if creators else ""
        meta = {"source": "Cleveland Museum of Art", "title": d.get("title") or "Untitled", "artist": (who.split(" (")[0] if who else "Unknown"),
                "date": d.get("creation_date") or "", "image_url": d["images"]["print"]["url"], "tags": [], "ref": f"cma:{d.get('id')}"}
    elif kind == "commons":
        info = _commons_info(c, key)
        if not info:
            return None
        meta = {"source": entry.get("source") or "Wikimedia Commons", "title": entry.get("title") or key, "artist": entry.get("artist") or "Unknown",
                "date": entry.get("date") or "", "image_url": info["url"], "tags": [], "ref": ref}
    else:
        return None
    for k in ("artist", "title", "date", "source"):
        if entry.get(k):
            meta[k] = entry[k]
    if not entry.get("allow") and not art._subject_safe(meta["title"], *meta.get("tags", []), strict=False):
        return None
    return meta


def _get_retry(c: httpx.Client, url: str, tries: int = 4):
    """Wikimedia and the Met both throttle with 429/403; back off and retry instead of dropping the work."""
    r = None
    for i in range(tries):
        r = c.get(url)
        if r.status_code not in (429, 403, 503):
            return r
        time.sleep(45 * (i + 1))
    return r


def named(list_path: str, theme: str, min_ratio: float = 1.2) -> None:
    from PIL import Image
    entries = json.loads(pathlib.Path(list_path).read_text())
    state = load_cands(); items = state["items"]
    seen_ref = {it["ref"] for it in items}; seen_sha = {it["sha1"] for it in items}
    n = max([it["n"] for it in items], default=0)
    CAND_DIR.mkdir(parents=True, exist_ok=True)
    c = httpx.Client(timeout=180, headers=art.UA, follow_redirects=True)
    added = 0
    print(f"named list {list_path}: {len(entries)} entries, theme={theme}", flush=True)
    for e in entries:
        if e["ref"] in seen_ref:
            print(f"  skip (have) {e['ref']}"); continue
        try:
            meta = resolve_named(c, e)
        except Exception as ex:
            meta = None; print(f"  error {e['ref']}: {type(ex).__name__}: {ex}")
        if not meta:
            print(f"  REJECTED/unavailable {e['ref']}  {e.get('title','')}"); continue
        try:
            r = _get_retry(c, meta["image_url"]); img = r.content
            if r.status_code != 200 or not img.startswith((b"\xff\xd8", b"\x89PNG")):
                print(f"  no image {e['ref']} ({r.status_code})"); continue
            if not art._landscape(img, min_ratio):
                print(f"  not landscape {e['ref']}  {meta['title'][:40]}"); continue
            sha = hashlib.sha1(img).hexdigest()
            if sha in seen_sha:
                print(f"  dup {e['ref']}"); continue
            w, h = Image.open(io.BytesIO(img)).size
        except Exception as ex:
            print(f"  download error {e['ref']}: {type(ex).__name__}"); continue
        n += 1; fn = CAND_DIR / f"{n:03d}.jpg"
        if img.startswith(b"\x89PNG"):
            Image.open(io.BytesIO(img)).convert("RGB").save(fn, "JPEG", quality=95)
        else:
            fn.write_bytes(img)
        it = dict(meta); it.update({"n": n, "sha1": sha, "w": w, "h": h, "file": fn.name, "status": "pending", "theme": theme,
                                    "term": f"named:{pathlib.Path(list_path).stem}", "watch": art.watch_hits(meta["title"], *meta.get("tags", []))})
        items.append(it); seen_ref.add(meta["ref"]); seen_sha.add(sha); added += 1
        print(f"  {n:3d}  {meta['source'][:9]:9s}  {meta['artist'][:28]:28s}  {meta['title'][:48]}  {w}x{h}", flush=True)
        save_cands(state)
        time.sleep(4.0 if meta["ref"].startswith("commons") else 0.6)
    print(f"\n{added} added ({len(items)} total). Rebuilding sheets...", flush=True)
    sheets()


if __name__ == "__main__":
    main()
