"""প্রতি 15 মিনিটের চক্রে: পুরো আয়তক্ষেত্র জুম 18-এ 285টি টুকরোয় তোলা হয়। টুকরোগুলো SHARDS-সংখ্যক সার্ভারে ভাগ করা,
প্রতিটি সার্ভার নিজের টুকরোর পিক্সেল গুনে data/shards/-এ লেখে। shard 0 অতিরিক্তভাবে জুম 15-এ পুরো এলাকার একটি ছবি তুলে Drive-এ পাঠায়।
Each 15-minute cycle: the rectangle is captured at ZOOM (18) in tiles, split across SHARDS parallel runners. Each shard
writes raw pixel counts for its tiles to data/shards/. Shard 0 also captures one OVERVIEW_ZOOM picture for Google Drive.
"""
import base64
import csv
import io
import os
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw

import config

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
LOCAL_SNAPS = ROOT / "snapshots"
DIV_CSV = DATA / "snapshots.csv"
THANA_CSV = DATA / "thana.csv"
HOT_CSV = DATA / "hotspots.csv"
DIV_FIELDS = ["timestamp", "date", "hour", "minute", "slot", "zone", "green", "orange", "red", "darkred",
              "score", "mode", "quality", "drive"]
THANA_FIELDS = ["timestamp", "date", "hour", "slot", "thana", "division", "green", "orange", "red", "darkred", "score"]
HOT_FIELDS = ["timestamp", "date", "hour", "minute", "slot", "hotspot", "green", "orange", "red", "darkred", "score", "drive"]
SHARD_DIR = DATA / "shards"
SHARD_FIELDS = ["timestamp", "date", "hour", "minute", "slot", "shard", "zone", "green", "orange", "red", "darkred",
                "tiles_ok", "tiles_total", "mode", "drive"]
SHARD_THANA_FIELDS = ["slot", "shard", "thana", "division", "green", "orange", "red", "darkred"]

TOTAL_SLOTS = int(config.COLLECTION_DAYS * 86400 / config.INTERVAL_SECONDS)
LIMIT = config.STORAGE_LIMIT_GB * 1e9
OVERVIEW_SHARE = 0.78 if config.HOTSPOTS else 0.97
OVERVIEW_MAX_BYTES = int(LIMIT * OVERVIEW_SHARE / TOTAL_SLOTS)
HOTSPOT_MAX_BYTES = int(LIMIT * 0.22 / (TOTAL_SLOTS / config.HOTSPOT_IMAGE_EVERY * max(1, len(config.HOTSPOTS))))

DMP_DIVS = list(config.DIVISIONS)
DIV_NAMES = DMP_DIVS + list(config.EXTRA_AREAS)
SEEDS = [(t, v[2], v[0], v[1]) for t, v in config.THANAS.items()]
for area, info in config.EXTRA_AREAS.items():
    SEEDS += [(area, area, la, ln) for la, ln in info["pts"]]
THANA_NAMES = list(dict.fromkeys(s[0] for s in SEEDS))
ZONE_OF_UNIT = {s[0]: s[1] for s in SEEDS}
S = config.OVERVIEW_SCALE


# ---------------------------------------------------------------- area maps (built once, at overview resolution)
def build_label_maps():
    h, w = config.OV_H, config.OV_W
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    best = np.full((h, w), np.inf, dtype=np.float32)
    unit = np.full((h, w), -1, dtype=np.int16)
    for name, _zone, lat, lng in SEEDS:
        px, py = config.latlng_to_ov(lat, lng)
        dist = (xx - px) ** 2 + (yy - py) ** 2
        closer = dist < best
        best[closer] = dist[closer]
        unit[closer] = THANA_NAMES.index(name)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon([config.latlng_to_ov(la, ln) for la, ln in config.STUDY_POLYGON], fill=1)
    unit[np.asarray(mask) == 0] = -1
    zone_of = np.array([DIV_NAMES.index(ZONE_OF_UNIT[u]) for u in THANA_NAMES], dtype=np.int16)
    zone = np.where(unit >= 0, zone_of[np.clip(unit, 0, None)], -1).astype(np.int16)
    return unit, zone


UNIT_OV, ZONE_OV = build_label_maps()


def tile_labels(tx, ty):
    """Zone/unit labels for one full-resolution tile, expanded from the overview maps."""
    k = int(round(1 / S))
    t = config.TILE // k
    u = UNIT_OV[ty * t:(ty + 1) * t, tx * t:(tx + 1) * t]
    z = ZONE_OV[ty * t:(ty + 1) * t, tx * t:(tx + 1) * t]
    return np.repeat(np.repeat(u, k, 0), k, 1), np.repeat(np.repeat(z, k, 0), k, 1)


def tiles_to_capture():
    k = int(round(1 / S))
    t = config.TILE // k
    out = []
    for ty in range(config.TILES_Y):
        for tx in range(config.TILES_X):
            if (ZONE_OV[ty * t:(ty + 1) * t, tx * t:(tx + 1) * t] >= 0).any():
                out.append((tx, ty))
    return out


TILES = tiles_to_capture()


def shard_tiles(shard):
    """Tiles handled by one shard (interleaved so every shard gets a similar mix of busy and empty areas)."""
    return TILES[shard::config.SHARDS]


# ---------------------------------------------------------------- colour analysis
def classify(rgb_uint8):
    """Masks for Google traffic colours: green (free), orange (slow), red (jam), dark red (near standstill)."""
    rgb = rgb_uint8.astype(np.float32) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx, mn = rgb.max(axis=-1), rgb.min(axis=-1)
    delta = mx - mn
    sat = np.where(mx > 0, delta / np.maximum(mx, 1e-6), 0)
    hue = np.zeros_like(mx)
    has = delta > 1e-6
    rmax = has & (mx == r)
    gmax = has & (mx == g) & ~rmax
    bmax = has & ~rmax & ~gmax
    hue[rmax] = ((g - b)[rmax] / delta[rmax]) % 6
    hue[gmax] = ((b - r)[gmax] / delta[gmax]) + 2
    hue[bmax] = ((r - g)[bmax] / delta[bmax]) + 4
    hue *= 60
    vivid = sat > 0.5
    red_hue = (hue < 18) | (hue >= 340)
    return (vivid & (hue >= 75) & (hue <= 165) & (mx > 0.45),
            vivid & (hue >= 18) & (hue < 55) & (mx > 0.6),
            vivid & red_hue & (mx >= 0.62),
            vivid & red_hue & (mx >= 0.2) & (mx < 0.62))


def remove_icons(masks):
    """Drops compact filled blobs (hospital/restaurant/no-entry icons, road-number shields); keeps road lines."""
    from scipy import ndimage
    anym = masks[0] | masks[1] | masks[2] | masks[3]
    lab, n = ndimage.label(anym)
    if n == 0:
        return masks
    drop = np.zeros(n + 1, dtype=bool)
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        if sl is None:
            continue
        hgt, wid = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if max(hgt, wid) <= 34 and min(hgt, wid) >= 11 and sizes[i] / float(hgt * wid) >= 0.45:
            drop[i] = True
    bad = drop[lab]
    return tuple(m & ~bad for m in masks)


def congestion_score(g, o, r, d):
    """0 = all green, 100 = all dark red. None if too few road pixels."""
    total = g + o + r + d
    if total < 30:
        return None
    return round(100 * (o + 2 * r + 3 * d) / (3 * total), 1)


def _counts_by(labels, n, masks):
    flat = labels.ravel()
    valid = flat >= 0
    return np.stack([np.bincount(flat[valid], weights=m.ravel()[valid], minlength=n) for m in masks], axis=1)


# ---------------------------------------------------------------- browser
API_HTML = """<!doctype html><html><head><style>html,body,#map{{margin:0;height:100%;width:100%}}</style></head>
<body><div id="map"></div><script>
function init(){{
  window.__map = new google.maps.Map(document.getElementById('map'), {{
    center: {{lat: 23.78, lng: 90.40}}, zoom: 14, disableDefaultUI: true, keyboardShortcuts: false,
    styles: [
      {{elementType: 'geometry', stylers: [{{saturation: -100}}, {{lightness: 35}}]}},
      {{elementType: 'labels', stylers: [{{visibility: 'off'}}]}},
      {{featureType: 'poi', stylers: [{{visibility: 'off'}}]}},
      {{featureType: 'transit', stylers: [{{visibility: 'off'}}]}}
    ]
  }});
  new google.maps.TrafficLayer().setMap(window.__map);
  window.__ready = true;
}}
window.moveTo = (lat, lng, z) => new Promise(res => {{
  google.maps.event.addListenerOnce(window.__map, 'tilesloaded', () => res(true));
  window.__map.setZoom(z); window.__map.setCenter({{lat: lat, lng: lng}});
  setTimeout(() => res(false), 30000);
}});
</script>
<script async src="https://maps.googleapis.com/maps/api/js?key={key}&callback=init"></script>
</body></html>"""

NO_MARGIN = {"left": 0, "top": 0, "right": 0, "bottom": 0}


class MapCamera:
    """Opens one browser and takes clipped screenshots of any square area at any zoom."""

    def __init__(self, pw):
        self.key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
        self.mode = "api" if self.key else "web"
        self.margin = NO_MARGIN if self.key else config.UI_MARGIN
        self.browser = pw.chromium.launch()
        self.page = None
        self.size = None
        self.first = True

    def _page(self, w, h):
        m = self.margin
        size = (w + m["left"] + m["right"], h + m["top"] + m["bottom"])
        if self.page is None:
            self.page = self.browser.new_page(viewport={"width": size[0], "height": size[1]},
                                              device_scale_factor=1, locale="en-US")
            if self.key:
                self.page.set_content(API_HTML.format(key=self.key))
                self.page.wait_for_function("window.__ready === true", timeout=60000)
        elif size != self.size:
            self.page.set_viewport_size({"width": size[0], "height": size[1]})
        self.size = size
        return self.page

    def shot(self, world_cx, world_cy, zoom, w, h):
        page = self._page(w, h)
        lat, lng = config.browser_center(world_cx, world_cy, zoom, self.margin)
        if self.key:
            page.evaluate(f"moveTo({lat}, {lng}, {zoom})")
            page.wait_for_timeout(3000)
        else:
            url = f"https://www.google.com/maps/@{lat:.7f},{lng:.7f},{zoom}z/data=!5m1!1e1?hl=en"
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            if self.first:
                for label in ("Accept all", "Reject all"):
                    try:
                        page.get_by_role("button", name=label).first.click(timeout=3000)
                        break
                    except Exception:
                        pass
            page.wait_for_timeout(config.PAGE_WAIT_FIRST if self.first else config.PAGE_WAIT)
        self.first = False
        m = self.margin
        png = page.screenshot(type="png", clip={"x": m["left"], "y": m["top"], "width": w, "height": h})
        return Image.open(io.BytesIO(png)).convert("RGB")

    def close(self):
        self.browser.close()


# ---------------------------------------------------------------- storage
def upload_to_drive(filename, date_folder, raw_bytes, mime, overwrite=False, subfolder=None):
    url = os.environ.get("DRIVE_WEBAPP_URL", "").strip()
    token = os.environ.get("DRIVE_TOKEN", "").strip()
    if not url:
        return "off"
    body = {"token": token, "filename": filename, "date": date_folder, "mime": mime, "overwrite": overwrite,
            "subfolder": subfolder or "", "data": base64.b64encode(raw_bytes).decode()}
    for attempt in range(3):
        try:
            js = requests.post(url, json=body, timeout=180).json()
            if js.get("ok"):
                return "ok"
            print(f"Drive said: {js}")
            return "error"
        except Exception as e:
            print(f"Drive upload attempt {attempt + 1} failed: {e}")
    return "error"


def encode_within(img, max_bytes):
    """JPEG that fits the storage budget: lowers quality first, then size."""
    scale = 1.0
    while True:
        im = img if scale == 1.0 else img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        for q in (85, 78, 70, 62, 55, 48, 40):
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=q, optimize=True)
            if buf.tell() <= max_bytes:
                return buf.getvalue()
        scale *= 0.85


def _append(path, fields, rows):
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def store_image(img, fname, when, max_bytes, subfolder=None):
    data = encode_within(img, max_bytes)
    drive = upload_to_drive(fname, when.strftime("%Y-%m-%d"), data, "image/jpeg", subfolder=subfolder)
    if drive != "ok":  # Drive not set up or failed: keep a small copy in the repository instead
        folder = LOCAL_SNAPS / (subfolder or "") / when.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        small = img.copy()
        small.thumbnail((900, 900))
        small.save(folder / fname, quality=70)
    return drive, len(data)


# ---------------------------------------------------------------- one full cycle
def run_cycle(when, slot, shard=0, camera_factory=None):
    """Captures this shard's tiles for one time slot (and, on shard 0, the Drive overview). Returns a summary dict."""
    from playwright.sync_api import sync_playwright

    DATA.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)
    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    zone_counts = np.zeros((len(DIV_NAMES), 4))
    unit_counts = np.zeros((len(THANA_NAMES), 4))
    my_tiles = shard_tiles(shard)
    ok_tiles, hot_rows, stored_bytes, drive, overview_crop = 0, [], 0, "", None

    with sync_playwright() as pw:
        cam = camera_factory(pw) if camera_factory else MapCamera(pw)
        mode = getattr(cam, "mode", "web")
        try:
            # 1) this shard's part of the rectangle at full zoom
            for tx, ty in my_tiles:
                cx = config.FRAME_X0 + (tx + 0.5) * config.TILE
                cy = config.FRAME_Y0 + (ty + 0.5) * config.TILE
                try:
                    img = cam.shot(cx, cy, config.ZOOM, config.TILE, config.TILE)
                except Exception as e:
                    print(f"tile {tx},{ty} failed: {e}")
                    continue
                masks = remove_icons(classify(np.asarray(img)))
                units, zones = tile_labels(tx, ty)
                zone_counts += _counts_by(zones, len(DIV_NAMES), masks)
                unit_counts += _counts_by(units, len(THANA_NAMES), masks)
                ok_tiles += 1

            # 2) shard 0 only: one picture of the whole rectangle for Google Drive
            if shard == 0:
                canvas = Image.new("RGB", (config.OVF_TILES_X * config.TILE, config.OVF_TILES_Y * config.TILE),
                                   (242, 242, 242))
                for ty in range(config.OVF_TILES_Y):
                    for tx in range(config.OVF_TILES_X):
                        cx = config.OVF_X0 + (tx + 0.5) * config.TILE
                        cy = config.OVF_Y0 + (ty + 0.5) * config.TILE
                        try:
                            canvas.paste(cam.shot(cx, cy, config.OVERVIEW_ZOOM, config.TILE, config.TILE),
                                         (tx * config.TILE, ty * config.TILE))
                        except Exception as e:
                            print(f"overview tile {tx},{ty} failed: {e}")
                overview_crop = canvas.crop((0, 0, config.OVF_W, config.OVF_H))

                # optional hotspots (empty by default)
                save_hot_images = slot % config.HOTSPOT_IMAGE_EVERY == 0
                for name, info in config.HOTSPOTS.items():
                    hx, hy = config.project(*info["at"], zoom=config.HOTSPOT_ZOOM)
                    try:
                        img = cam.shot(hx, hy, config.HOTSPOT_ZOOM, config.HOTSPOT_SIZE, config.HOTSPOT_SIZE)
                    except Exception as e:
                        print(f"hotspot {name} failed: {e}")
                        continue
                    c = [int(m.sum()) for m in remove_icons(classify(np.asarray(img)))]
                    hdrive = "skipped"
                    if save_hot_images:
                        fname = f"{name.replace(' ', '_')}_{when:%Y-%m-%d_%H-%M-%S}_BDT_z{config.HOTSPOT_ZOOM}.jpg"
                        hdrive, nbytes = store_image(img, fname, when, HOTSPOT_MAX_BYTES, subfolder="hotspots")
                        stored_bytes += nbytes
                    hot_rows.append({"timestamp": when.isoformat(), "date": when.date().isoformat(), "hour": when.hour,
                                     "minute": when.minute, "slot": slot, "hotspot": name, "green": c[0],
                                     "orange": c[1], "red": c[2], "darkred": c[3],
                                     "score": _s(congestion_score(*c)), "drive": hdrive})
        finally:
            cam.close()

    if overview_crop is not None:
        fname = f"Dhaka_traffic_{when:%Y-%m-%d_%H-%M-%S}_BDT_z{config.OVERVIEW_ZOOM}.jpg"
        drive, nbytes = store_image(overview_crop, fname, when, OVERVIEW_MAX_BYTES)
        stored_bytes += nbytes
        latest = overview_crop.copy()
        latest.thumbnail((1000, 1330))
        latest.save(DOCS / "latest.jpg", quality=80)
        division_overlay(overview_crop).save(DOCS / "divisions_check.jpg", quality=80)

    # raw counts for this shard; analyze.py adds all shards together and computes the scores
    total = zone_counts.sum(axis=0)
    dmp = zone_counts[:len(DMP_DIVS)].sum(axis=0)
    rows = {"Study area (all)": total, "DMP (all)": dmp, **{n: zone_counts[i] for i, n in enumerate(DIV_NAMES)}}
    base = {"timestamp": when.isoformat(), "date": when.date().isoformat(), "hour": when.hour, "minute": when.minute,
            "slot": slot, "shard": shard}
    _append(SHARD_DIR / f"shard_{shard:02d}.csv", SHARD_FIELDS, [
        {**base, "zone": z, "green": int(c[0]), "orange": int(c[1]), "red": int(c[2]), "darkred": int(c[3]),
         "tiles_ok": ok_tiles, "tiles_total": len(my_tiles), "mode": mode, "drive": drive}
        for z, c in rows.items()])
    _append(SHARD_DIR / f"shard_{shard:02d}_thana.csv", SHARD_THANA_FIELDS, [
        {"slot": slot, "shard": shard, "thana": t, "division": ZONE_OF_UNIT[t], "green": int(c[0]),
         "orange": int(c[1]), "red": int(c[2]), "darkred": int(c[3])}
        for t, c in zip(THANA_NAMES, unit_counts)])
    if hot_rows:
        _append(HOT_CSV, HOT_FIELDS, hot_rows)
    return {"score": congestion_score(*total), "tiles_ok": ok_tiles, "tiles": len(my_tiles), "drive": drive or "-",
            "bytes": stored_bytes, "mode": mode}


def _s(v):
    return "" if v is None else v


def division_overlay(crop):
    """Colour-tinted check image so you can confirm the division areas sit in the right places."""
    im = crop.copy()
    im.thumbnail((1000, 1200))
    zw, zh = im.size
    z = np.asarray(Image.fromarray(ZONE_OV[:int(config.VIEW_H * S), :int(config.VIEW_W * S)].astype(np.int32) + 1)
                   .resize((zw, zh), Image.NEAREST)) - 1
    arr = np.asarray(im).astype(float)
    for i, name in enumerate(DIV_NAMES):
        hexc = config.DIVISIONS.get(name, {"color": config.EXTRA_COLOR})["color"].lstrip("#")
        rgb = np.array([int(hexc[k:k + 2], 16) for k in (0, 2, 4)])
        a = 0.3 if name in config.DIVISIONS else 0.2
        arr[z == i] = arr[z == i] * (1 - a) + rgb * a
    out = Image.fromarray(arr.astype(np.uint8))
    d = ImageDraw.Draw(out)
    for i, name in enumerate(DIV_NAMES):
        ys, xs = np.nonzero(z == i)
        if len(xs):
            d.text((int(xs.mean()) - 20, int(ys.mean())), name, fill=(0, 0, 0))
    for name, info in config.HOTSPOTS.items():
        x, y = config.latlng_to_overview(*info["at"])
        x, y = x * zw / crop.width, y * zh / crop.height
        d.rectangle([x - 4, y - 4, x + 4, y + 4], outline=(0, 0, 0), width=2)
    return out
