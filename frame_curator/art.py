"""Public-domain artwork → Samsung The Frame, locally (no Art Store subscription, no cloud).

Sources: open-access works from The Met, the Cleveland Museum of Art, the Art Institute of Chicago (metadata only,
see note below) and Wikimedia Commons, no API keys. Each work is fitted whole onto the panel's native 3840×2160 with
a white gallery mat and an engraved brass plaque (Pillow), then pushed to the TV over its local art websocket
(samsungtvws, port 8002) and selected in Art Mode.

Pairing is one-time: the first connection makes the TV show an Allow prompt; accept it with the remote and the token
is saved to <data dir>/frame_<ip>.token. `frame-setup` does that interactively.
"""
from __future__ import annotations

import hashlib
import io
import os
import pathlib
import random
import re

import httpx

# Everything the tool writes (candidates, contact sheets, gallery, manifest, pairing token) lives here.
DATA_DIR = pathlib.Path(os.getenv("FRAME_DATA_DIR", "data")).resolve()
SIZE = (3840, 2160)          # the panel's native resolution (4K Frame)
import os as _os

# FAMILY-SAFE canon (docs/frame-family-gallery.md, 2026-10-13): serene subjects only. Monet gardens/water lilies,
# Impressionist landscapes & city scenes, Dutch/Barbizon landscapes & seascapes, still lifes, architecture, horses &
# countryside, calm pastoral/genre. NO religious, battle, mythology, nude or modern terms — ever. The search terms are
# only the first gate; `_subject_safe` below is the hard gate on every candidate's title/tags/description.
# Override with ART_TERMS="a,b,c".
_DEFAULT_TERMS = [
    # Monet & Giverny
    "Monet water lilies", "Monet Giverny", "Monet garden", "Monet haystacks", "Monet poplars",
    "Monet Rouen Cathedral", "Monet Argenteuil", "Monet Vétheuil", "Monet Étretat", "Monet Antibes", "Monet Venice",
    "Monet Houses of Parliament", "Monet Bordighera", "water lilies",
    # Impressionist landscapes & city scenes
    "Alfred Sisley", "Camille Pissarro", "Gustave Caillebotte", "Eugène Boudin", "Johan Barthold Jongkind",
    "Armand Guillaumin", "Berthe Morisot garden", "Renoir landscape", "impressionist landscape", "Paris boulevard",
    "Seine river", "Normandy coast", "Honfleur harbor", "Trouville beach",
    # Dutch & Barbizon landscapes / seascapes
    "Dutch landscape", "Hague School", "Barbizon", "Corot landscape", "Daubigny", "Théodore Rousseau landscape",
    "Troyon cattle", "Jacob van Ruisdael", "Meindert Hobbema", "Aelbert Cuyp", "Jan van Goyen", "Willem van de Velde",
    "Hendrick Avercamp winter", "windmill", "Dutch seascape", "Salomon van Ruysdael",
    # still lifes
    "still life flowers", "still life fruit", "Fantin-Latour flowers", "Jan van Huysum", "Rachel Ruysch",
    "Chardin still life", "Jan Davidsz de Heem", "bouquet of flowers", "basket of fruit",
    # architecture & cities
    "Canaletto Venice", "Francesco Guardi Venice", "Venice canal", "cathedral", "village street", "bridge river",
    # horses & countryside, pastoral
    "horses countryside", "Rosa Bonheur", "John Constable", "Turner landscape", "Gainsborough landscape",
    "George Stubbs horses", "cattle meadow", "sheep pasture", "harvest field", "haymaking", "shepherd flock",
    "cottage landscape", "snow landscape", "river landscape", "harbor boats", "sunset landscape", "orchard",
]
TERMS = [t.strip() for t in _os.getenv("ART_TERMS", "").split(",") if t.strip()] or _DEFAULT_TERMS


def _is_painting(text: str) -> bool:
    return "paint" in (text or "").lower()


# Keep the theme to European Renaissance–Baroque oil/tempera; drop Asian/Islamic miniatures & manuscript folios that
# these "Painting"-typed collections also return.
EXCLUDE = (
    "india", "indian", "persia", "persian", "mughal", "rajput", "basohli", "pahari", "deccan", "kangra", "bhagavata",
    "purana", "ramayana", "china", "chinese", "qing", "ming", "japan", "japanese", "edo", "korea", "korean", "tibet",
    "tibetan", "nepal", "nepalese", "islamic", "ottoman", "qajar", "safavid", "mamluk", "arab", "turkish", "thai",
    "burmese", "javanese", "vietnam", "mongol", "manuscript", "folio", "miniature", "opaque watercolor", "ink and color",
    "page from", "album leaf", "album", "sutra", "hindu", "jain", "buddhis", "handscroll", "hanging scroll",
    # not Western canon (Eastern Orthodox / Byzantine icon traditions)
    "russian", "byzantine", "coptic", "ethiopian", "orthodox", "icon of",
)


def _excluded(*parts: str) -> bool:
    t = " ".join(str(p) for p in parts if p).lower()
    return any(k in t for k in EXCLUDE)


EUROPE = ("europe", "italy", "italian", "france", "french", "netherl", "dutch", "flem", "flanders", "spain", "spanish",
          "germany", "german", "england", "english", "british", "austria", "belg", "greece", "greek", "roman", "rome",
          "florence", "florentine", "venice", "venetian", "naples", "neapolitan", "bologna", "sevilla", "antwerp")


def _european(*parts: str) -> bool:
    t = " ".join(str(p) for p in parts if p).lower()
    return any(k in t for k in EUROPE)


# ---------- subject gate (family-safe) ----------
# Hard-exclude on title / museum subject tags / description: religious martyrdom, any beheading, battles, ALL
# mythology (it carries nudity), any nude, death imagery. Substring match, lowercase. Deliberately broad: a false
# reject costs nothing, a false accept ends up on the living-room wall in front of children.
SUBJECT_BLOCK_CORE = (
    # Christian / religious narrative
    # ("saint " / "sainte " with a space: Saint Jerome is blocked, the hyphenated place names Sainte-Adresse,
    #  Saint-Mammès, Saint-Lazare stay allowed)
    "crucifix", "christ", "jesus", "madonna", "virgin", "saint ", "saint,", "st. ", "sainte ", "sainte,", " holy ", "martyr",
    "pieta", "pietà", "lamentation", "deposition", "annunciation", "nativity", "adoration", "resurrection",
    "baptism", "apostle", "angel", "judgment", "judgement", "last supper", "altarpiece", "bible", "biblical",
    "moses", "goliath", "judith", "holofernes", "salome", "john the baptist", "magdalen", "ecce homo",
    "flagellation", "agony in", "sacrifice", "abraham", "isaac", "samson", " cain ", " abel ", "apocalypse", " hell",
    "devil", "demon", "witch", "temptation", "prophet", "evangelist", "crusade", "christian", "religio", " god",
    # beheading / gore / death
    "head of", "severed", "decapit", "behead", "execution", "massacre", "murder", "wounded", "death", "dead",
    "dying", "deathbed", "funeral", "grave", "tomb", "skull", "vanitas", "plague", "corpse", "blood", "slaughter",
    "torture", "suicide", "killing", "hanged", "prisoner",
    # mythology / allegory (nudity carriers)
    # (word-bounded where a place name collides: Marseille, Toledo, floral)
    "venus", "diana", "apollo", "bacch", "cupid", "nymph", "satyr", "europa", " leda", "danae", "danaë",
    "andromeda", "perseus", "hercules", " mars ", " mars,", "jupiter", "zeus", "neptune", "galatea", "psyche", "mytholog",
    "allegor", " muse ", " muses", "goddess", "aphrodite", "aurora", " flora ", " flora,", "pan ", "triton", "nereid", "ariadne",
    "helen", "ulysses", "odysseus", "aeneas", "dido", "orpheus", "narcissus", "pandora", "prometheus", "olymp",
    # nudity
    "nude", "naked", "bather", "bathing", "odalisque", "susanna", "lucretia", "lucrece", "cleopatra", "rape",
    "abduction", "sabine", "slave", "harem", "toilet of", "la toilette", "model", "reclining", "undress",
    "torso", "erotic",
    # modern / avant-garde (belt and braces on top of the era gate)
    "expressionis", "cubis", "surreal", "abstract", "fauv", "dada",
)
# Strict extras: military/hunting subjects. Applied to everything EXCEPT an explicitly named list where the owner
# chose the works (e.g. Americana history paintings such as Washington Crossing the Delaware, which carry
# "Soldiers" tags but no gore). The CORE block (nudity, gore, death, religious narrative, mythology) always applies.
SUBJECT_BLOCK_STRICT = (
    "battle", "siege", "cavalry", "war ", "warrior", "soldier", "army", "combat", "duel", "troops", "military",
    "cannon", "musket", "sword", "weapon", "hunt", "chase",
)
SUBJECT_BLOCK = SUBJECT_BLOCK_CORE + SUBJECT_BLOCK_STRICT
# Museum subject/tag vocabularies (AIC subject_titles, Met AAT tags) that are safe by construction: a candidate from a
# tagged source must carry at least one of these (in its tags, or failing that in its title).
SAFE_TAGS = (
    "landscape", "seascape", "still life", "still lifes", "flower", "fruit", "garden", "water lilies", "nymphaea",
    "harbor", "harbour", "boat", "ship", "river", "bridge", "snow", "winter", "cityscape", "city scene", "street",
    "architecture", "building", "cathedral", "church", "horse", "cow", "cattle", "sheep", "tree", "forest",
    "coast", "beach", "countryside", "haystack", "pond", "sunset", "sunrise", "mountain", "village", "farm",
    "field", "meadow", "lake", "cloud", "sky", "mill", "canal", "marsh", "dune", "valley", "orchard", "vase",
    "bouquet", "rose", "peon", "chrysanth", "dahlia", "apple", "peach", "grape", "pear", "path", "road", "shore",
    "sea", "cliff", "poplar", "water", "plant", "foliage", "weather", "season", "towns", "houses", "castle",
    "windmill", "cottage", "barn", "wheat", "harvest", "pasture", "animal", "duck", "swan", "bird",
)
# tags that mean "look very carefully": figures in landscapes are usually fine, but these are where nudity hides
WATCH_TAGS = ("nude", "women", "woman", "men", "man", "figure", "bather", "body", "girl", "boy", "child", "people")


def _subject_safe(*parts, strict: bool = True) -> bool:
    """True when nothing in the title/tags/description trips the hard block list (CORE always; STRICT by default)."""
    t = " " + " ".join(str(p) for p in parts if p).lower() + " "
    return not any(k in t for k in (SUBJECT_BLOCK if strict else SUBJECT_BLOCK_CORE))


def _has_safe_tag(tags) -> bool:
    t = " ".join(str(x) for x in (tags or [])).lower()
    return any(k in t for k in SAFE_TAGS)


def watch_hits(*parts) -> list[str]:
    t = " ".join(str(p) for p in parts if p).lower()
    return [k for k in WATCH_TAGS if k in t]


UA = {"User-Agent": "frame-curator/0.1 (+https://github.com/jaselton/frame-curator)"}

# ---------- era / modernist gate ----------
# The user's canon is Old-Master religious/history/mythology + French Impressionism — i.e. nothing after the turn of
# the century EXCEPT the named Impressionists. The museum "European painting" departments also hold 20th-c modernism
# (Expressionism, Cubism, Surrealism, etc.), which must be blocked. Two instruments: a hard date backstop (>1915) and
# a surname blocklist for the 1900–1915 avant-garde, with an allowlist that lets the late Impressionists through.
MODERN_BLOCK = (
    "beckmann", "kirchner", "nolde", "kokoschka", "schiele", "egon schiele", "munch", "ensor", "otto dix", " dix",
    "george grosz", " grosz", "kandinsky", "franz marc", "august macke", "jawlensky", "feininger", "heckel",
    "schmidt-rottluff", "pechstein", "picasso", "braque", "juan gris", "leger", "léger", "modigliani", "chagall",
    "soutine", "rouault", "matisse", "derain", "vlaminck", "raoul dufy", "de chirico", "dali", "dalí", "miro", "miró",
    "max ernst", "magritte", "paul klee", "mondrian", "malevich", "boccioni", "severini", "balla", "duchamp",
    "picabia", "kupka", "delaunay", "larionov", "goncharova", "schwitters", "beckman", "corinth", "liebermann",
    "kollwitz", "nolde", "hofer", "meidner", "campendonk", "mueller",
)
IMPRESSIONIST_OK = (
    "monet", "renoir", "degas", "pissarro", "sisley", "caillebotte", "manet", "morisot", "cassatt", "bazille",
    "boudin", "guillaumin",
)


def _year(*parts) -> int | None:
    t = " ".join(str(p) for p in parts if p)
    yrs = [int(y) for y in re.findall(r"(1[0-9]{3}|20[0-2][0-9])", t)]
    return max(yrs) if yrs else None


def _era_ok(artist: str, *dateparts) -> bool:
    a = (artist or "").lower()
    if any(k in a for k in MODERN_BLOCK):
        return False
    if any(k in a for k in IMPRESSIONIST_OK):
        return True
    y = _year(*dateparts)
    if y is not None and y > 1915:
        return False
    return True



# ---------- artwork sources (CC0 / open access, no key) ----------
MET_SEARCH = "https://collectionapi.metmuseum.org/public/collection/v1.1/search"   # v1 search retired 2026-10-01
MET_OBJECT = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{}"
MET_DEPT_EUROPEAN_PAINTINGS = 11


def met_object_meta(o: dict) -> dict | None:
    """Apply every gate (public domain, image, European Paintings, painting, era, Western, family-safe subject) to a
    Met object record; return our metadata dict or None."""
    tags = [t.get("term", "") for t in (o.get("tags") or [])]
    if not (o.get("isPublicDomain") and o.get("primaryImage")
            and o.get("department") in ("European Paintings", "Robert Lehman Collection")
            and _is_painting(o.get("classification", ""))
            and _era_ok(o.get("artistDisplayName"), o.get("objectDate"), o.get("objectEndDate"), o.get("objectBeginDate"))
            and not _excluded(o.get("culture"), o.get("medium"), o.get("period"), o.get("title"), o.get("artistNationality"))
            and _subject_safe(o.get("title"), *tags)
            and (_has_safe_tag(tags) or _has_safe_tag([o.get("title")]))):
        return None
    return {"source": "The Met", "title": o.get("title") or "Untitled",
            "artist": o.get("artistDisplayName") or "Unknown", "date": o.get("objectDate") or "",
            "image_url": o["primaryImage"], "tags": tags, "ref": f"met:{o.get('objectID')}"}


def _met(c: httpx.Client, term: str) -> dict | None:
    # HARD Western gate: only the Met's "European Paintings" department (the Old-Master canon).
    r = c.get(MET_SEARCH, params={"q": term, "hasImages": "true", "isPublicDomain": "true",
                                  "departmentId": MET_DEPT_EUROPEAN_PAINTINGS, "limit": 40})
    ids = (r.json() or {}).get("objectIDs") or []
    random.shuffle(ids)
    for oid in ids[:40]:
        try:
            meta = met_object_meta(c.get(MET_OBJECT.format(oid)).json())
        except Exception:
            continue
        if meta:
            return meta
    return None


# NOTE 2026-10-13: artic.edu's IIIF image server now sits behind a Cloudflare browser challenge (HTTP 403 for any
# non-browser client), so AIC metadata still works but its images do not download. Kept for when that changes; the
# fetchers skip it automatically because the response is HTML, not JPEG.
AIC_FIELDS = "id,title,artist_title,date_display,image_id,classification_title,place_of_origin,department_title,medium_display,subject_titles"


def artic_item_meta(d: dict) -> dict | None:
    tags = d.get("subject_titles") or []
    if not (d.get("image_id") and _is_painting(d.get("classification_title", ""))
            and "Europe" in (d.get("department_title") or "")
            and _era_ok(d.get("artist_title"), d.get("date_display"))
            and not _excluded(d.get("place_of_origin"), d.get("medium_display"), d.get("title"))
            and _subject_safe(d.get("title"), *tags)
            and (_has_safe_tag(tags) or _has_safe_tag([d.get("title")]))):
        return None
    return {"source": "Art Institute of Chicago", "title": d.get("title") or "Untitled",
            "artist": d.get("artist_title") or "Unknown", "date": d.get("date_display") or "",
            "image_url": f"https://www.artic.edu/iiif/2/{d['image_id']}/full/1920,/0/default.jpg",
            "tags": tags, "ref": f"aic:{d.get('id')}"}


def _artic(c: httpx.Client, term: str) -> dict | None:
    r = c.get("https://api.artic.edu/api/v1/artworks/search",
              params={"q": term, "query[term][is_public_domain]": "true", "fields": AIC_FIELDS, "limit": 60})
    items = (r.json() or {}).get("data", [])
    # HARD Western gate: department "Painting and Sculpture of Europe"
    data = [m for m in (artic_item_meta(d) for d in items) if m]
    return random.choice(data) if data else None


CLEVELAND_DEPTS = ("European Painting and Sculpture", "Modern European Painting and Sculpture")


def cleveland_item_meta(d: dict) -> dict | None:
    if not (d.get("images") or {}).get("print"):
        return None
    cult = " ".join(d.get("culture") or [])
    creators = d.get("creators") or []
    who = creators[0].get("description", "") if creators else ""
    desc = d.get("description") or ""
    if not (_european(cult) and _era_ok(who, d.get("creation_date"))
            and not _excluded(cult, d.get("technique"), d.get("title"))
            and _subject_safe(d.get("title"), d.get("tombstone"), desc[:600])):
        return None
    return {"source": "Cleveland Museum of Art", "title": d.get("title") or "Untitled",
            "artist": (who.split(" (")[0] if who else "Unknown"), "date": d.get("creation_date") or "",
            "image_url": d["images"]["print"]["url"], "tags": [], "ref": f"cma:{d.get('id')}"}


def _cleveland(c: httpx.Client, term: str) -> dict | None:
    data = []
    for dept in CLEVELAND_DEPTS:
        r = c.get("https://openaccess-api.clevelandart.org/api/artworks/",
                  params={"cc0": "1", "has_image": "1", "q": term, "type": "Painting", "department": dept, "limit": 60})
        data += [m for m in (cleveland_item_meta(d) for d in (r.json() or {}).get("data", [])) if m]
    return random.choice(data) if data else None


def fetch_artwork(term: str | None = None) -> dict:
    """Pick one public-domain work (title/artist/source + raw image bytes). Tries several themed terms across the three
    museums until one yields a usable high-res image."""
    # a named term gets several rounds (results are randomised each query) so a landscape example is actually found
    terms = [term] * 6 if term else random.sample(TERMS, min(6, len(TERMS)))
    with httpx.Client(timeout=25, headers=UA, follow_redirects=True) as c:
        for t in terms:
            for fn in random.sample((_met, _artic, _cleveland), 3):
                try:
                    meta = fn(c, t)
                    if not meta:
                        continue
                    img = c.get(meta["image_url"])
                    img.raise_for_status()
                    if not img.content.startswith((b"\xff\xd8", b"\x89PNG")):
                        continue
                    if not _landscape(img.content):   # skip portrait/near-square — they look small on the 16:9 65"
                        continue
                    meta["image"] = img.content
                    return meta
                except Exception:
                    continue
    raise RuntimeError("no artwork could be fetched from any source")


MAT = (242, 240, 234)        # soft white, like Samsung's "polar" mat
MAT_LINE = (158, 156, 150)   # thin neutral bevel line around the opening
MAT_MARGIN = 0.095           # mat border as a fraction of the panel (Samsung-ish width)


_FONT_DIRS = ("/System/Library/Fonts/Supplemental/", "/System/Library/Fonts/")


def _font(name: str, size: int):
    from PIL import ImageFont
    for d in _FONT_DIRS:
        try:
            return ImageFont.truetype(d + name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _plaque(canvas, meta: dict, art_bottom: int) -> None:
    """A small polished-brass museum plaque, engraved with artist + title/date, centred in the bottom mat."""
    from PIL import ImageDraw
    d = ImageDraw.Draw(canvas)
    tw, th = SIZE
    artist = (meta.get("artist") or "Unknown").strip()
    title = (meta.get("title") or "Untitled").strip()
    date = (meta.get("date") or "").strip()
    if len(title) > 52:
        title = title[:51].rstrip() + "…"
    line2 = title + (f", {date}" if date else "")
    f1, f2 = _font("Georgia Bold.ttf", 34), _font("Georgia Italic.ttf", 28)

    def size(txt, f):
        b = d.textbbox((0, 0), txt, font=f)
        return b[2] - b[0], b[3] - b[1]
    w1, h1 = size(artist, f1)
    w2, h2 = size(line2, f2)
    padx, pady, gap = 30, 15, 7
    pw, ph = max(w1, w2) + 2 * padx, h1 + gap + h2 + 2 * pady
    cx = tw // 2
    band_top = art_bottom + 10
    top = min(max((band_top + th) // 2 - ph // 2, band_top), th - ph - 16)
    x0, x1 = cx - pw // 2, cx + pw // 2
    y0, y1 = top, top + ph
    d.rounded_rectangle([x0, y0, x1, y1], radius=10, fill=(184, 149, 54), outline=(110, 86, 24), width=2)
    d.line([x0 + 3, y0 + 2, x1 - 3, y0 + 2], fill=(226, 202, 124), width=2)   # top highlight
    d.line([x0 + 3, y1 - 2, x1 - 3, y1 - 2], fill=(118, 92, 28), width=2)     # bottom shadow
    ink = (40, 30, 8)
    d.text((cx, y0 + pady + h1 // 2), artist, font=f1, fill=ink, anchor="mm")
    d.text((cx, y0 + pady + h1 + gap + h2 // 2), line2, font=f2, fill=ink, anchor="mm")


def prepare(data: bytes, meta: dict | None = None) -> bytes:
    """Fit the WHOLE painting inside 3840×2160 with a white gallery mat + thin bevel line (Samsung Art style, nothing
    cropped), and — when meta is given — a brass plaque engraved with artist + title/date in the bottom mat."""
    from PIL import Image, ImageDraw
    im = Image.open(io.BytesIO(data)).convert("RGB")
    tw, th = SIZE
    maxw, maxh = int(tw * (1 - 2 * MAT_MARGIN)), int(th * (1 - 2 * MAT_MARGIN))
    scale = min(maxw / im.width, maxh / im.height)
    nw, nh = round(im.width * scale), round(im.height * scale)
    im = im.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", SIZE, MAT)
    x, y = (tw - nw) // 2, (th - nh) // 2
    canvas.paste(im, (x, y))
    ImageDraw.Draw(canvas).rectangle([x - 5, y - 5, x + nw + 4, y + nh + 4], outline=MAT_LINE, width=2)
    if meta:
        _plaque(canvas, meta, y + nh)
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90)
    return out.getvalue()


# ---------- upload manifest (data/frame_uploads.json) ----------
# content_id -> {artist, title, date, source, image_url, sha1, ref, uploaded_at}. Written by every uploader so a piece
# can always be traced back and removed surgically (docs/frame-family-gallery.md).
MANIFEST = DATA_DIR / "frame_uploads.json"


def manifest_load(path: pathlib.Path = MANIFEST) -> dict:
    import json
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def manifest_save(m: dict, path: pathlib.Path = MANIFEST) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=1, ensure_ascii=False, sort_keys=True))
    tmp.replace(path)


def manifest_record(content_id: str, meta: dict, image: bytes | None = None, path: pathlib.Path = MANIFEST) -> None:
    import datetime as _dt
    m = manifest_load(path)
    m[content_id] = {k: meta.get(k) for k in ("artist", "title", "date", "source", "image_url", "ref")}
    m[content_id]["sha1"] = meta.get("sha1") or (hashlib.sha1(image).hexdigest() if image else None)
    m[content_id]["uploaded_at"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    manifest_save(m, path)


def manifest_forget(content_ids, path: pathlib.Path = MANIFEST) -> None:
    m = manifest_load(path)
    for cid in content_ids:
        m.pop(cid, None)
    manifest_save(m, path)


def _landscape(data: bytes, min_ratio: float = 1.3) -> bool:
    """True if the image is comfortably landscape (so it fills the 65" without looking like a small portrait)."""
    try:
        from PIL import Image
        w, h = Image.open(io.BytesIO(data)).size
        return w >= h * min_ratio
    except Exception:
        return False


# ---------- The Frame controller (synchronous; call via asyncio.to_thread) ----------
class FrameTV:
    def __init__(self, host: str, token_file: str | None = None) -> None:
        self.host = host
        self.token_file = token_file or str(DATA_DIR / f"frame_{host}.token")

    def _art(self):
        from samsungtvws import SamsungTVWS
        tv = SamsungTVWS(host=self.host, port=8002, token_file=self.token_file, name="HomeDisplayArt", timeout=20)
        return tv.art()

    @staticmethod
    def reachable(host: str) -> bool:
        try:
            with httpx.Client(timeout=3) as c:
                return c.get(f"http://{host}:8001/api/v2/").status_code == 200
        except Exception:
            return False

    def info(self) -> dict:
        """Device name/model/MAC, used to tell the 65" from the 43" and to wake it."""
        try:
            with httpx.Client(timeout=3) as c:
                dev = c.get(f"http://{self.host}:8001/api/v2/").json().get("device", {})
            return {"name": (dev.get("name") or "").replace("[TV] ", ""), "model": dev.get("modelName"),
                    "frame": dev.get("FrameTVSupport"), "power": dev.get("PowerState"), "mac": dev.get("wifiMac")}
        except Exception:
            return {}

    def wake(self, mac: str | None = None) -> bool:
        """Wake-on-LAN. The 2019 Frame closes its control ports (8001/8002) in Art-Mode deep sleep; a magic packet to
        its MAC brings them back for ~a minute. Pass the MAC, or let it read wifiMac from the REST info."""
        import socket
        mac = mac or self.info().get("mac")
        if not mac:
            return False
        raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
        if len(raw) != 6:
            return False
        pkt = b"\xff" * 6 + raw * 16
        targets = ["255.255.255.255"] + [b for b in [os.getenv("FRAME_WOL_BROADCAST", "")] if b]
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for p in (9, 7):
                for b in targets:
                    s.sendto(pkt, (b, p))
            s.close()
            return True
        except Exception:
            return False

    def _call(self, action, tries: int = 5):
        """Run action(art_channel) with a fresh connection, retrying the 2019 Frame's spurious `clientConnect`
        handshake failure (and transient socket drops)."""
        import time as _t
        from samsungtvws.exceptions import ConnectionFailure
        err = None
        for _ in range(tries):
            try:
                return action(self._art())
            except ConnectionFailure as e:
                err = e; _t.sleep(1.2)
            except Exception as e:
                err = e; _t.sleep(0.8)
        raise err if err else RuntimeError("art call failed")

    def supported(self) -> bool:
        return bool(self._call(lambda a: a.supported()))

    def upload_one(self, jpeg: bytes) -> str:
        return self._call(lambda a: a.upload(jpeg, file_type="JPEG", matte="none"))

    def upload_pool(self, jpegs: list[bytes]) -> list[str]:
        """Upload several works (the gallery the TV's slideshow rotates). Returns the content_ids that stuck."""
        ids = []
        for j in jpegs:
            try:
                ids.append(self.upload_one(j))
            except Exception:
                continue
        return ids

    def select(self, content_id: str, art_mode: bool = True) -> None:
        """Display an already-uploaded piece (no re-upload)."""
        self._call(lambda a: a.select_image(content_id, show=True))
        if art_mode:
            try:
                self._call(lambda a: a.set_artmode("on"), tries=2)
            except Exception:
                pass

    def show(self, jpeg: bytes, prev_id: str | None = None, art_mode: bool = True) -> str:
        """Upload a JPEG, display it, and remove the previous upload. Returns the new content_id."""
        content_id = self.upload_one(jpeg)
        self.select(content_id, art_mode=art_mode)
        if prev_id and prev_id != content_id:
            self.delete([prev_id])
        return content_id

    def enable_slideshow(self, minutes: int = 30) -> bool:
        """Turn on the TV's own Art-Mode slideshow (shuffle uploaded art). Only runs in Art Mode, so it never
        interrupts live TV. category=2 = uploaded 'My Photos'."""
        try:
            self._call(lambda a: a.set_slideshow_status(duration=minutes, type=True, category=2), tries=3)
            return True
        except Exception:
            return False

    def my_uploads(self) -> list[str]:
        """content_ids currently uploaded to the TV (so old galleries can be pruned)."""
        try:
            items = self._call(lambda a: a.available(), tries=3) or []
            cids = [it.get("content_id") for it in items if str(it.get("content_id", "")).startswith("MY_")]
            return list(dict.fromkeys(cids))   # available() can list a piece under more than one category — dedupe
        except Exception:
            return []

    def delete(self, content_ids: list[str]) -> None:
        if not content_ids:
            return
        try:
            self._call(lambda a: a.delete_list(list(content_ids)), tries=2)
        except Exception:
            pass


def image_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:12]
