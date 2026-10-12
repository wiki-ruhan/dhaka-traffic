"""একটি স্ন্যাপশট নেয়, ট্রাফিক রং গুনে বিভাগ/থানাভিত্তিক স্কোর বানায়, এবং ছবি Google Drive-এ পাঠায়।
Takes one Google Maps traffic snapshot of the DMP area, scores congestion per DMP traffic division and thana,
and uploads the time-stamped image to Google Drive (through your Apps Script web app).
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
DIV_FIELDS = ["timestamp", "date", "hour", "minute", "slot", "zone", "green", "orange", "red", "darkred",
              "score", "mode", "quality", "drive"]
THANA_FIELDS = ["timestamp", "date", "hour", "slot", "thana", "division", "green", "orange", "red", "darkred", "score"]

DMP_DIVS = list(config.DIVISIONS)
DIV_NAMES = DMP_DIVS + list(config.EXTRA_AREAS)          # 8 DMP divisions + areas outside DMP
# seed points: (unit name, zone name). DMP thanas are units of their division; each extra area is its own unit.
SEEDS = [(t, v[2], v[0], v[1]) for t, v in config.THANAS.items()]
for area, info in config.EXTRA_AREAS.items():
    SEEDS += [(area, area, la, ln) for la, ln in info["pts"]]
THANA_NAMES = list(dict.fromkeys(s[0] for s in SEEDS))
ZONE_OF_UNIT = {s[0]: s[1] for s in SEEDS}


# ---------------------------------------------------------------- area map (which pixel belongs where)
def study_mask():
    m = Image.new("L", (config.VIEW_W, config.VIEW_H), 0)
    ImageDraw.Draw(m).polygon([config.latlng_to_px(la, ln) for la, ln in config.STUDY_POLYGON], fill=1)
    return np.asarray(m).astype(bool)


def build_label_maps():
    h, w = config.VIEW_H, config.VIEW_W
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    best = np.full((h, w), np.inf, dtype=np.float32)
    unit_idx = np.full((h, w), -1, dtype=np.int16)
    for unit, _zone, lat, lng in SEEDS:
        px, py = config.latlng_to_px(lat, lng)
        dist = (xx - px) ** 2 + (yy - py) ** 2
        closer = dist < best
        best[closer] = dist[closer]
        unit_idx[closer] = THANA_NAMES.index(unit)
    unit_idx[~study_mask()] = -1                       # outside the study area
    zone_of = np.array([DIV_NAMES.index(ZONE_OF_UNIT[u]) for u in THANA_NAMES], dtype=np.int16)
    div_idx = np.where(unit_idx >= 0, zone_of[np.clip(unit_idx, 0, None)], -1).astype(np.int16)
    return unit_idx, div_idx


THANA_IDX, DIV_IDX = build_label_maps()


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
    return (vivid & (hue >= 75) & (hue <= 165) & (mx > 0.45),      # green
            vivid & (hue >= 18) & (hue < 55) & (mx > 0.6),          # orange
            vivid & red_hue & (mx >= 0.62),                         # red
            vivid & red_hue & (mx >= 0.2) & (mx < 0.62))            # dark red


def congestion_score(g, o, r, d):
    """0 = all green, 100 = all dark red. None if too few road pixels."""
    total = g + o + r + d
    if total < 30:
        return None
    return round(100 * (o + 2 * r + 3 * d) / (3 * total), 1)


def _counts_by(labels, n, masks):
    out = []
    flat = labels.ravel()
    valid = flat >= 0
    for m in masks:
        out.append(np.bincount(flat[valid], weights=m.ravel()[valid], minlength=n).astype(int))
    return np.stack(out, axis=1)  # n x 4


def analyse(img):
    arr = np.asarray(img.convert("RGB").resize((config.VIEW_W, config.VIEW_H)))
    masks = classify(arr)
    inside = DIV_IDX >= 0
    in_dmp = inside & (DIV_IDX < len(DMP_DIVS))
    total = [int((m & inside).sum()) for m in masks]
    dmp = [int((m & in_dmp).sum()) for m in masks]
    div_counts = _counts_by(DIV_IDX, len(DIV_NAMES), masks)
    thana_counts = _counts_by(THANA_IDX, len(THANA_NAMES), masks)
    divisions = {"Study area (all)": total + [congestion_score(*total)],
                 "DMP (all)": dmp + [congestion_score(*dmp)]}
    for i, name in enumerate(DIV_NAMES):
        c = div_counts[i].tolist()
        divisions[name] = c + [congestion_score(*c)]
    thanas = {}
    for i, name in enumerate(THANA_NAMES):
        c = thana_counts[i].tolist()
        thanas[name] = c + [congestion_score(*c)]
    return divisions, thanas


def division_overlay(img):
    """Colour-tinted check image so you can confirm the division areas sit in the right places."""
    base = img.convert("RGB").resize((config.VIEW_W, config.VIEW_H))
    tint = np.zeros((config.VIEW_H, config.VIEW_W, 3), dtype=np.uint8)
    alpha = np.zeros((config.VIEW_H, config.VIEW_W), dtype=np.float32)
    for i, name in enumerate(DIV_NAMES):
        hexc = config.DIVISIONS.get(name, {"color": config.EXTRA_COLOR})["color"].lstrip("#")
        tint[DIV_IDX == i] = [int(hexc[k:k + 2], 16) for k in (0, 2, 4)]
        alpha[DIV_IDX == i] = 0.28 if name in config.DIVISIONS else 0.18
    out = (np.asarray(base) * (1 - alpha[..., None]) + tint * alpha[..., None]).astype(np.uint8)
    im = Image.fromarray(out)
    d = ImageDraw.Draw(im)
    for i, name in enumerate(DIV_NAMES):
        ys, xs = np.nonzero(DIV_IDX == i)
        if len(xs):
            d.text((int(xs.mean()) - 20, int(ys.mean())), name, fill=(0, 0, 0))
    return im


# ---------------------------------------------------------------- browser
API_HTML = """<!doctype html><html><head><style>html,body,#map{{margin:0;height:100%;width:100%}}</style></head>
<body><div id="map"></div><script>
function init(){{
  const map = new google.maps.Map(document.getElementById('map'), {{
    center: {{lat: {lat}, lng: {lng}}}, zoom: {zoom}, disableDefaultUI: true, keyboardShortcuts: false,
    styles: [
      {{elementType: 'geometry', stylers: [{{saturation: -100}}, {{lightness: 35}}]}},
      {{elementType: 'labels', stylers: [{{visibility: 'off'}}]}},
      {{featureType: 'poi', stylers: [{{visibility: 'off'}}]}},
      {{featureType: 'transit', stylers: [{{visibility: 'off'}}]}}
    ]
  }});
  new google.maps.TrafficLayer().setMap(map);
  google.maps.event.addListenerOnce(map, 'tilesloaded', () => {{ window.__tiles = true; }});
}}
</script>
<script async src="https://maps.googleapis.com/maps/api/js?key={key}&callback=init"></script>
</body></html>"""


def take_snapshot():
    """Returns (PIL image, mode). Mode 'api' if GOOGLE_MAPS_API_KEY is set, otherwise 'web'."""
    from playwright.sync_api import sync_playwright

    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    lat, lng = config.CENTER_LAT, config.CENTER_LNG
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": config.VIEW_W, "height": config.VIEW_H},
                                device_scale_factor=1, locale="en-US")
        if key:
            mode = "api"
            page.set_content(API_HTML.format(lat=lat, lng=lng, zoom=config.ZOOM, key=key))
            page.wait_for_function("window.__tiles === true", timeout=60000)
            page.wait_for_timeout(6000)
        else:
            mode = "web"
            url = f"https://www.google.com/maps/@{lat:.6f},{lng:.6f},{config.ZOOM}z/data=!5m1!1e1?hl=en"
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            for label in ("Accept all", "Reject all"):
                try:
                    page.get_by_role("button", name=label).first.click(timeout=3000)
                    break
                except Exception:
                    pass
            page.wait_for_timeout(12000)
        png = page.screenshot(type="png")
        browser.close()
    return Image.open(io.BytesIO(png)), mode


# ---------------------------------------------------------------- storage
def upload_to_drive(filename, date_folder, raw_bytes, mime, overwrite=False, subfolder=None):
    """Sends a file to your Apps Script web app, which saves it in Google Drive."""
    url = os.environ.get("DRIVE_WEBAPP_URL", "").strip()
    token = os.environ.get("DRIVE_TOKEN", "").strip()
    if not url:
        return "off"
    body = {"token": token, "filename": filename, "date": date_folder, "mime": mime, "overwrite": overwrite,
            "subfolder": subfolder or "", "data": base64.b64encode(raw_bytes).decode()}
    for attempt in range(3):
        try:
            r = requests.post(url, json=body, timeout=120)
            js = r.json()
            if js.get("ok"):
                return "ok"
            print(f"Drive said: {js}")
            return "error"
        except Exception as e:
            print(f"Drive upload attempt {attempt + 1} failed: {e}")
    return "error"


def save_snapshot(img, mode, when, slot):
    divisions, thanas = analyse(img)
    road_pixels = sum(divisions["Study area (all)"][:4])
    quality = "ok" if road_pixels > 1500 else "low_traffic_pixels"

    stamp = when.strftime("%Y-%m-%d_%H-%M-%S")
    fname = f"Dhaka_traffic_{stamp}_BDT.{config.DRIVE_IMAGE_FORMAT}"
    buf = io.BytesIO()
    if config.DRIVE_IMAGE_FORMAT == "png":
        img.save(buf, "PNG", optimize=True)
        mime = "image/png"
    else:
        img.convert("RGB").save(buf, "JPEG", quality=config.JPG_QUALITY)
        mime = "image/jpeg"
    drive = upload_to_drive(fname, when.strftime("%Y-%m-%d"), buf.getvalue(), mime)

    # if Drive is not set up, keep a smaller copy in the GitHub repo instead
    if drive != "ok":
        LOCAL_SNAPS.mkdir(exist_ok=True)
        small = img.convert("RGB")
        small.thumbnail((800, 1100))
        (LOCAL_SNAPS / when.strftime("%Y-%m-%d")).mkdir(exist_ok=True)
        small.save(LOCAL_SNAPS / when.strftime("%Y-%m-%d") / fname.replace(".png", ".jpg"), quality=75)

    DATA.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)
    base = {"timestamp": when.isoformat(), "date": when.date().isoformat(), "hour": when.hour, "slot": slot}
    _append(DIV_CSV, DIV_FIELDS, [
        {**base, "minute": when.minute, "zone": z, "green": c[0], "orange": c[1], "red": c[2], "darkred": c[3],
         "score": "" if c[4] is None else c[4], "mode": mode, "quality": quality, "drive": drive}
        for z, c in divisions.items()])
    _append(THANA_CSV, THANA_FIELDS, [
        {**base, "thana": t, "division": ZONE_OF_UNIT[t], "green": c[0], "orange": c[1], "red": c[2],
         "darkred": c[3], "score": "" if c[4] is None else c[4]}
        for t, c in thanas.items()])

    latest = img.convert("RGB")
    latest.thumbnail((800, 1100))
    latest.save(DOCS / "latest.jpg", quality=80)
    ov = division_overlay(img)
    ov.thumbnail((800, 1100))
    ov.save(DOCS / "divisions_check.jpg", quality=80)
    return divisions["Study area (all)"][4], quality, drive


def _append(path, fields, rows):
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)
