"""প্রতিটি স্লটে: এলাকাটি config.py-এর জুমে (এখন 17) টুকরো টুকরো করে তোলা হয়, প্রতিটি টুকরোর রঙ গোনা হয়, টুকরোগুলো জোড়া লাগিয়ে
পূর্ণ রেজোলিউশনের একটি ছবি বানানো হয়, নিচে ইংরেজি footer (সেকেন্ডসহ সময়, তাপমাত্রা, বৃষ্টি, AQI, বাতাস) যোগ করা হয়,
তারপর ছবিটি Google Drive-এ পাঠানো হয়।

Per slot: capture the rectangle at the configured zoom (now 17) in tiles, count traffic colours per zone, stitch a full-resolution image
(or one horizontal strip per shard), add an English footer below the map, write metadata, and upload to Google Drive
through the user's Apps Script web app (it hands out a short-lived Drive token; the file itself goes straight to the
Drive API in resumable chunks, so there is no size limit).
"""
import base64
import csv
import io
import json
import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

import config

Image.MAX_IMAGE_PIXELS = None   # the stitched images are very large on purpose

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
WORK = ROOT / "work"            # images wait here until uploaded (never committed to git)
SHARD_DIR = DATA / "shards"
TZ = ZoneInfo(config.TIMEZONE)
SHARD_FIELDS = ["timestamp", "date", "hour", "minute", "slot", "shard", "zone", "green", "orange", "red", "darkred",
                "tiles_ok", "tiles_total", "mode", "captured_at", "capture_end"]
SHARD_THANA_FIELDS = ["slot", "shard", "thana", "division", "green", "orange", "red", "darkred"]
COND_FIELDS = ["slot", "timestamp", "temp_c", "feels_c", "humidity", "rain_1h_mm", "weather", "wind_kmh",
               "wind_dir_deg", "wind_dir", "gust_kmh", "us_aqi", "aqi_category", "pm2_5", "pm10", "aqi_source"]
UPLOAD_FIELDS = ["slot", "shard", "file", "bytes", "status", "seconds", "finished_at", "error"]

DMP_DIVS = list(config.DIVISIONS)
DIV_NAMES = DMP_DIVS + list(config.EXTRA_AREAS)
SEEDS = [(t, v[2], v[0], v[1]) for t, v in config.THANAS.items()]
for area, info in config.EXTRA_AREAS.items():
    SEEDS += [(area, area, la, ln) for la, ln in info["pts"]]
THANA_NAMES = list(dict.fromkeys(s[0] for s in SEEDS))
ZONE_OF_UNIT = {s[0]: s[1] for s in SEEDS}
K = config.LABEL_FACTOR


# ---------------------------------------------------------------- area maps (built once)
def build_label_maps():
    h, w = config.LAB_H, config.LAB_W
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    best = np.full((h, w), np.inf, dtype=np.float32)
    unit = np.full((h, w), -1, dtype=np.int16)
    for name, _zone, lat, lng in SEEDS:
        px, py = config.latlng_to_ov(lat, lng)
        dist = (xx - px) ** 2 + (yy - py) ** 2
        closer = dist < best
        best[closer] = dist[closer]
        unit[closer] = THANA_NAMES.index(name)
    inside = np.zeros((h, w), dtype=bool)
    inside[:int(np.ceil(config.VIEW_H / K)), :int(np.ceil(config.VIEW_W / K))] = True
    unit[~inside] = -1
    zone_of = np.array([DIV_NAMES.index(ZONE_OF_UNIT[u]) for u in THANA_NAMES], dtype=np.int16)
    zone = np.where(unit >= 0, zone_of[np.clip(unit, 0, None)], -1).astype(np.int16)
    return unit, zone


UNIT_OV, ZONE_OV = build_label_maps()
ZONES_PRESENT = [DIV_NAMES[i] for i in sorted(set(np.unique(ZONE_OV)) - {-1})]


def tile_labels(tx, ty):
    t = config.TILE // K
    u = UNIT_OV[ty * t:(ty + 1) * t, tx * t:(tx + 1) * t]
    z = ZONE_OV[ty * t:(ty + 1) * t, tx * t:(tx + 1) * t]
    return np.repeat(np.repeat(u, K, 0), K, 1), np.repeat(np.repeat(z, K, 0), K, 1)


def shard_rows(shard):
    r0 = shard * config.ROWS_PER_SHARD
    return range(r0, min(r0 + config.ROWS_PER_SHARD, config.TILES_Y))


def shard_tiles(shard):
    return [(tx, ty) for ty in shard_rows(shard) for tx in range(config.TILES_X)]


ALL_TILES = [t for s in range(config.SHARDS) for t in shard_tiles(s)]


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
    center: {{lat: 23.78, lng: 90.40}}, zoom: 14, disableDefaultUI: true, keyboardShortcuts: false
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


# ---------------------------------------------------------------- weather, rain, wind, AQI
WMO = {0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
       80: "light showers", 81: "showers", 82: "violent showers", 95: "thunderstorm", 96: "thunderstorm, hail",
       99: "thunderstorm, hail"}
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def aqi_category(v):
    if v is None:
        return None
    for limit, name in ((50, "Good"), (100, "Moderate"), (150, "Unhealthy for Sensitive Groups"),
                        (200, "Unhealthy"), (300, "Very Unhealthy")):
        if v <= limit:
            return name
    return "Hazardous"


def fetch_conditions(when):
    """Current weather and air quality for the centre of the study area. Never raises."""
    lat = config.WEATHER_LAT or config.CENTER_LAT
    lng = config.WEATHER_LNG or config.CENTER_LNG
    c = {k: None for k in COND_FIELDS}
    c["slot"], c["timestamp"] = config.slot_id(when), when.isoformat()
    try:
        js = requests.get("https://api.open-meteo.com/v1/forecast", timeout=20, params={
            "latitude": lat, "longitude": lng, "timezone": config.TIMEZONE, "past_hours": 1, "forecast_hours": 1,
            "hourly": "precipitation",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,"
                       "wind_speed_10m,wind_direction_10m,wind_gusts_10m"}).json()
        cur = js["current"]
        c.update(temp_c=cur.get("temperature_2m"), feels_c=cur.get("apparent_temperature"),
                 humidity=cur.get("relative_humidity_2m"), wind_kmh=cur.get("wind_speed_10m"),
                 wind_dir_deg=cur.get("wind_direction_10m"), gust_kmh=cur.get("wind_gusts_10m"),
                 weather=WMO.get(cur.get("weather_code"), f"code {cur.get('weather_code')}"))
        if c["wind_dir_deg"] is not None:
            c["wind_dir"] = COMPASS[int((c["wind_dir_deg"] % 360) / 22.5 + 0.5) % 16]
        hour = when.strftime("%Y-%m-%dT%H:00")
        hrs = js.get("hourly", {})
        if hour in hrs.get("time", []):
            c["rain_1h_mm"] = hrs["precipitation"][hrs["time"].index(hour)]
        else:
            c["rain_1h_mm"] = cur.get("precipitation")
    except Exception as e:
        print(f"Weather unavailable: {type(e).__name__}")
    try:
        js = requests.get("https://air-quality-api.open-meteo.com/v1/air-quality", timeout=20, params={
            "latitude": lat, "longitude": lng, "timezone": config.TIMEZONE, "current": "us_aqi,pm2_5,pm10"}).json()
        cur = js["current"]
        c.update(us_aqi=cur.get("us_aqi"), pm2_5=cur.get("pm2_5"), pm10=cur.get("pm10"), aqi_source="model")
    except Exception as e:
        print(f"Air quality unavailable: {type(e).__name__}")
    token = os.environ.get("WAQI_TOKEN", "").strip()
    if token:  # optional: measured value from the Dhaka (US Embassy) monitoring station
        try:
            js = requests.get(f"https://api.waqi.info/feed/dhaka/?token={token}", timeout=20).json()
            if js.get("status") == "ok" and isinstance(js["data"].get("aqi"), (int, float)):
                c["us_aqi"] = js["data"]["aqi"]
                c["aqi_source"] = "station: " + js["data"].get("city", {}).get("name", "Dhaka")[:40]
        except Exception as e:
            print(f"WAQI unavailable: {type(e).__name__}")
    if c["us_aqi"] is not None:
        c["us_aqi"] = int(round(c["us_aqi"]))
    c["aqi_category"] = aqi_category(c["us_aqi"])
    return c


# ---------------------------------------------------------------- footer and metadata
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/dejavu/", "C:/Windows/Fonts/", "/Library/Fonts/"]


def _font(size, bold=False):
    names = ["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"] if bold else ["DejaVuSans.ttf", "arial.ttf", "Arial.ttf"]
    for d in FONT_DIRS:
        for n in names:
            p = Path(d) / n
            if p.exists():
                return ImageFont.truetype(str(p), size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _v(x, fmt="{}", na="n/a"):
    return na if x is None else fmt.format(x)


def footer_lines(info):
    c = info["conditions"]
    t0, t1 = info["captured_at"], info["capture_end"]
    strip = f"  |  Strip {info['shard'] + 1} of {config.SHARDS}" if config.SHARDS > 1 else ""
    b = config.BOUNDS
    return [
        f"Dhaka Traffic Study  |  {t0:%Y-%m-%d} ({t0:%a})  {t0:%H:%M:%S} BDT (UTC+6)  |  Slot {info['slot']}{strip}",
        f"Capture window {t0:%H:%M:%S} - {t1:%H:%M:%S} BDT  |  Tiles {info['tiles_ok']}/{info['tiles_total']}  |  "
        f"Congestion score {_v(info['score'], '{:.1f}')} (0-100)  |  Zoom {config.ZOOM} "
        f"({config.metres_per_px(config.CENTER_LAT):.2f} m/px)",
        f"Temperature {_v(c['temp_c'], '{:.1f}')} °C  |  Feels like {_v(c['feels_c'], '{:.1f}')} °C  |  "
        f"Humidity {_v(c['humidity'], '{:.0f}')}%  |  Rain {_v(c['rain_1h_mm'], '{:.1f}')} mm (last hour)  |  "
        f"Weather: {_v(c['weather'])}",
        f"Wind {_v(c['wind_kmh'], '{:.0f}')} km/h from {_v(c['wind_dir'])} ({_v(c['wind_dir_deg'], '{:.0f}')}°), "
        f"gusts {_v(c['gust_kmh'], '{:.0f}')} km/h  |  US AQI {_v(c['us_aqi'])} ({_v(c['aqi_category'])})  |  "
        f"PM2.5 {_v(c['pm2_5'], '{:.0f}')} µg/m³  |  PM10 {_v(c['pm10'], '{:.0f}')} µg/m³  |  "
        f"AQI source: {_v(c['aqi_source'])}",
        f"Area N {b['north']:.4f}  S {b['south']:.4f}  W {b['west']:.4f}  E {b['east']:.4f}  |  "
        f"Map data © Google  |  Legend:",
    ]


LEGEND = [((99, 214, 104), "Fast"), ((255, 151, 77), "Slow"), ((242, 60, 50), "Jam"), ((129, 31, 31), "Standstill")]


FOOTER_LINES = 5


def footer_size(width, map_h=None):
    """Font and strip size: readable when the whole image is viewed, but never taller than ~12% of the map."""
    fs = max(22, width // 95)
    if map_h:
        fs = max(22, min(fs, map_h // 80))
    pad, lh = fs, int(fs * 1.5)
    return fs, pad, lh, pad * 2 + lh * FOOTER_LINES


def draw_footer(out, map_h, info):
    """Writes the footer into the white strip below the map (rows map_h and down). Map pixels are never touched."""
    w = out.width
    fs, pad, lh, _ = footer_size(w, map_h)
    font, bold = _font(fs), _font(fs, bold=True)
    lines = footer_lines(info)
    d = ImageDraw.Draw(out)
    y = map_h + pad
    d.line([(0, map_h), (w, map_h)], fill=(60, 60, 60), width=max(2, fs // 10))
    for i, text in enumerate(lines):
        d.text((pad, y), text, fill=(25, 25, 25), font=bold if i == 0 else font)
        if i == len(lines) - 1:  # colour squares for the legend
            x = pad + d.textlength(text, font=font) + fs
            for colour, name in LEGEND:
                d.rectangle([x, y + fs * 0.15, x + fs, y + fs * 1.15], fill=colour)
                x += fs * 1.4
                d.text((x, y), name, fill=(25, 25, 25), font=font)
                x += d.textlength(name, font=font) + fs * 1.2
        y += lh


def save_image(img, path, info):
    """Saves PNG (lossless) or JPEG with the timestamp written into the file's metadata."""
    t0 = info["captured_at"]
    meta = {"slot": info["slot"], "captured_at": t0.isoformat(), "capture_end": info["capture_end"].isoformat(),
            "bounds": config.BOUNDS, "zoom": config.ZOOM, "score": info["score"], "shard": info["shard"],
            "conditions": info["conditions"]}
    fmt = config.IMAGE_FORMAT.lower()
    if fmt == "webp" and max(img.size) > 16383:
        fmt = "jpg"  # WebP cannot store more than 16,383 px per side
    if fmt == "jpg" and max(img.size) > 65000:
        fmt = "png"  # JPEG cannot store more than 65,535 px per side
    path = path.with_suffix({"webp": ".webp", "jpg": ".jpg"}.get(fmt, ".png"))
    exif = Image.Exif()
    exif[0x0132] = t0.strftime("%Y:%m:%d %H:%M:%S")                 # DateTime
    exif[0x010E] = f"Dhaka traffic slot {info['slot']}"             # ImageDescription
    ifd = exif.get_ifd(0x8769)
    ifd[0x9003] = t0.strftime("%Y:%m:%d %H:%M:%S")                  # DateTimeOriginal
    ifd[0x9011] = "+06:00"                                          # OffsetTimeOriginal
    if fmt == "webp":
        try:
            img.save(path, "WEBP", quality=config.WEBP_QUALITY, method=config.WEBP_METHOD, exif=exif.tobytes())
        except (ValueError, OSError) as e:  # image too large for WebP: fall back to JPEG
            print(f"WebP failed ({e}); saving as JPEG")
            path.unlink(missing_ok=True)
            fmt, path = "jpg", path.with_suffix(".jpg")
    if fmt == "jpg":
        img.save(path, "JPEG", quality=config.JPG_QUALITY, optimize=True, progressive=True, exif=exif)
    elif fmt == "png":
        pi = PngImagePlugin.PngInfo()
        pi.add_text("Creation Time", t0.isoformat())
        pi.add_text("Title", f"Dhaka traffic {t0:%Y-%m-%d %H:%M:%S} BDT")
        pi.add_text("Comment", json.dumps(meta, ensure_ascii=False))
        img.save(path, "PNG", pnginfo=pi, compress_level=config.PNG_COMPRESS_LEVEL, optimize=False)
    side = path.with_suffix(".json")
    side.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return path, side


# ---------------------------------------------------------------- Google Drive
class Drive:
    """Uploads through the user's Apps Script web app.

    The web app (running as the user) returns a short-lived Drive access token and folder IDs. Files are then sent
    straight to the Google Drive API with resumable uploads, so there is no size limit and no extra software.
    """

    API = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,size"

    def __init__(self):
        self.url = os.environ.get("DRIVE_WEBAPP_URL", "").strip()
        self.secret = os.environ.get("DRIVE_TOKEN", "").strip()
        self.enabled = bool(self.url)
        self._token, self._token_time, self._folders = None, 0, {}
        self._lock = threading.Lock()

    def _call(self, **payload):
        r = requests.post(self.url, json={"token": self.secret, **payload}, timeout=120)
        js = r.json()
        if not js.get("ok"):
            raise RuntimeError(f"Apps Script: {js}")
        return js

    def access_token(self):
        with self._lock:
            if not self._token or time.time() - self._token_time > 40 * 60:
                self._token = self._call(action="token")["access_token"]
                self._token_time = time.time()
            return self._token

    def folder(self, *names):
        key = (config.DRIVE_ROOT,) + tuple(n for n in names if n)
        with self._lock:
            if key not in self._folders:
                self._folders[key] = self._call(action="folder", path=list(key))["id"]
            return self._folders[key]

    def upload(self, path, folder_names, mime):
        size = path.stat().st_size
        headers = {"Authorization": f"Bearer {self.access_token()}", "Content-Type": "application/json; charset=UTF-8",
                   "X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)}
        r = requests.post(self.API, headers=headers, timeout=60,
                          json={"name": path.name, "parents": [self.folder(*folder_names)]})
        r.raise_for_status()
        session = r.headers["Location"]
        chunk = config.UPLOAD_CHUNK_MB * 1024 * 1024  # multiple of 256 KiB, as the API requires
        with path.open("rb") as f:
            start = 0
            while start < size:
                data = f.read(chunk)
                end = start + len(data) - 1
                for attempt in range(5):
                    try:
                        r = requests.put(session, data=data, timeout=300, headers={
                            "Authorization": f"Bearer {self.access_token()}",
                            "Content-Range": f"bytes {start}-{end}/{size}"})
                        if r.status_code in (200, 201, 308):
                            break
                        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                    except Exception as e:
                        if attempt == 4:
                            raise
                        print(f"chunk retry {attempt + 1}: {e}")
                        time.sleep(5 * (attempt + 1))
                start = end + 1
        if r.status_code not in (200, 201):
            raise RuntimeError(f"upload did not finish: HTTP {r.status_code}")
        return size

    def small_file(self, name, raw_bytes, mime, subfolder):
        """Small files (CSV backups) go through the web app itself and replace the older copy."""
        js = self._call(filename=name, date="", mime=mime, overwrite=True, subfolder=subfolder,
                        data=base64.b64encode(raw_bytes).decode())
        return js


class Uploader:
    """Uploads images in the background so the next capture is not delayed."""

    def __init__(self, shard):
        self.shard = shard
        self.drive = Drive()
        self.q = queue.Queue()
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.thread.start()

    def add(self, slot, files, date_folder):
        self.q.put((slot, files, date_folder))

    def _log(self, row):
        SHARD_DIR.mkdir(parents=True, exist_ok=True)
        _append(SHARD_DIR / f"uploads_{self.shard:02d}.csv", UPLOAD_FIELDS, [row])

    def _work(self):
        while True:
            slot, files, date_folder = self.q.get()
            for path, mime in files:
                t0 = time.time()
                row = {"slot": slot, "shard": self.shard, "file": path.name, "bytes": path.stat().st_size,
                       "status": "", "seconds": "", "finished_at": "", "error": ""}
                if not self.drive.enabled:
                    row.update(status="kept_local", error="DRIVE_WEBAPP_URL not set")
                else:
                    try:
                        self.drive.upload(path, [date_folder], mime)
                        row["status"] = "ok"
                        path.unlink(missing_ok=True)
                    except Exception as e:
                        row.update(status="error", error=str(e)[:200])
                        print(f"Upload failed for {path.name}: {e}")
                row.update(seconds=round(time.time() - t0, 1), finished_at=datetime.now(TZ).isoformat())
                self._log(row)
            self.q.task_done()

    def drain(self, timeout=None):
        """Waits until every queued file is uploaded (or the timeout passes)."""
        t0 = time.time()
        while self.q.unfinished_tasks:
            if timeout and time.time() - t0 > timeout:
                print(f"{self.q.unfinished_tasks} upload(s) still pending")
                return False
            time.sleep(2)
        return True


def _append(path, fields, rows):
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- one slot for one shard
def run_cycle(when, shard=0, uploader=None, camera_factory=None):
    """Captures this shard's rows, analyses them, saves the stitched image with footer, queues upload."""
    from playwright.sync_api import sync_playwright

    for d in (DATA, DOCS, WORK, SHARD_DIR):
        d.mkdir(parents=True, exist_ok=True)
    slot = config.slot_id(when)
    conditions = fetch_conditions(when)
    rows = list(shard_rows(shard))
    tiles = shard_tiles(shard)
    y_top = rows[0] * config.TILE
    strip_h = min(config.VIEW_H, (rows[-1] + 1) * config.TILE) - y_top
    footer_h = footer_size(config.VIEW_W, strip_h)[3]
    canvas = Image.new("RGB", (config.VIEW_W, strip_h + footer_h), (255, 255, 255))   # one image: map + footer
    canvas.paste((242, 242, 242), (0, 0, config.VIEW_W, strip_h))
    zone_counts = np.zeros((len(DIV_NAMES), 4))
    unit_counts = np.zeros((len(THANA_NAMES), 4))
    ok_tiles, captured_at = 0, None

    with sync_playwright() as pw:
        cam = camera_factory(pw) if camera_factory else MapCamera(pw)
        mode = getattr(cam, "mode", "web")
        try:
            for tx, ty in tiles:
                cx = config.FRAME_X0 + (tx + 0.5) * config.TILE
                cy = config.FRAME_Y0 + (ty + 0.5) * config.TILE
                try:
                    t_shot = datetime.now(TZ).replace(microsecond=0)
                    img = cam.shot(cx, cy, config.ZOOM, config.TILE, config.TILE)
                    captured_at = captured_at or t_shot
                except Exception as e:
                    print(f"tile {tx},{ty} failed: {e}")
                    continue
                masks = remove_icons(classify(np.asarray(img)))
                units, zones = tile_labels(tx, ty)
                zone_counts += _counts_by(zones, len(DIV_NAMES), masks)
                unit_counts += _counts_by(units, len(THANA_NAMES), masks)
                # tiles at the right/bottom edge reach past the study area: keep only the part inside it
                box_w = min(config.TILE, config.VIEW_W - tx * config.TILE)
                box_h = min(config.TILE, strip_h - (ty * config.TILE - y_top))
                canvas.paste(img.crop((0, 0, box_w, box_h)), (tx * config.TILE, ty * config.TILE - y_top))
                ok_tiles += 1
                del img, masks, units, zones
        finally:
            cam.close()
    capture_end = datetime.now(TZ).replace(microsecond=0)
    captured_at = captured_at or capture_end

    total = zone_counts.sum(axis=0)
    info = {"slot": slot, "shard": shard, "captured_at": captured_at, "capture_end": capture_end,
            "tiles_ok": ok_tiles, "tiles_total": len(tiles), "score": congestion_score(*total),
            "conditions": conditions}

    # image: map + footer, with metadata, then queued for upload
    draw_footer(canvas, strip_h, info)
    final = canvas
    ext = config.IMAGE_FORMAT.lower()
    suffix = f"_strip{shard + 1:02d}of{config.SHARDS:02d}" if config.SHARDS > 1 else ""
    name = f"Dhaka_traffic_{captured_at:%Y-%m-%d_%H-%M-%S}_BDT_z{config.ZOOM}{suffix}.{ext}"
    img_path, side_path = save_image(final, WORK / name, info)
    image_bytes = img_path.stat().st_size
    if shard == 0:
        thumb = final.copy()
        thumb.thumbnail((1200, 1600))
        thumb.save(DOCS / "latest.jpg", quality=82)
        division_overlay(canvas.crop((0, 0, config.VIEW_W, strip_h)), rows).save(DOCS / "divisions_check.jpg",
                                                                                   quality=80)
    del final, canvas
    if uploader:
        mime = {".webp": "image/webp", ".jpg": "image/jpeg"}.get(img_path.suffix, "image/png")
        uploader.add(slot, [(img_path, mime), (side_path, "application/json")], f"{when:%Y-%m-%d}")

    # raw counts for this shard; analyze.py adds the shards together and computes the scores
    dmp = zone_counts[:len(DMP_DIVS)].sum(axis=0)
    zrows = {"Study area (all)": total, "DMP (all)": dmp,
             **{n: zone_counts[i] for i, n in enumerate(DIV_NAMES) if n in ZONES_PRESENT}}
    base = {"timestamp": when.isoformat(), "date": when.date().isoformat(), "hour": when.hour, "minute": when.minute,
            "slot": slot, "shard": shard, "tiles_ok": ok_tiles, "tiles_total": len(tiles), "mode": mode,
            "captured_at": captured_at.isoformat(), "capture_end": capture_end.isoformat()}
    _append(SHARD_DIR / f"shard_{shard:02d}.csv", SHARD_FIELDS, [
        {**base, "zone": z, "green": int(c[0]), "orange": int(c[1]), "red": int(c[2]), "darkred": int(c[3])}
        for z, c in zrows.items()])
    _append(SHARD_DIR / f"shard_{shard:02d}_thana.csv", SHARD_THANA_FIELDS, [
        {"slot": slot, "shard": shard, "thana": t, "division": ZONE_OF_UNIT[t], "green": int(c[0]),
         "orange": int(c[1]), "red": int(c[2]), "darkred": int(c[3])}
        for t, c in zip(THANA_NAMES, unit_counts) if c.sum() > 0])
    if shard == 0:
        _append(DATA / "conditions.csv", COND_FIELDS, [conditions])
    return {"score": info["score"], "tiles_ok": ok_tiles, "tiles": len(tiles), "bytes": image_bytes,
            "file": img_path.name, "mode": mode, "captured_at": captured_at, "capture_end": capture_end}


def division_overlay(canvas, rows):
    """Colour-tinted check image so you can confirm the zone areas sit in the right places."""
    im = canvas.copy()
    im.thumbnail((1000, 1400))
    zw, zh = im.size
    t = config.TILE // K
    z = ZONE_OV[rows[0] * t:(rows[-1] + 1) * t, :int(np.ceil(config.VIEW_W / K))]
    z = z[:int(np.ceil(canvas.height / K))]
    z = np.asarray(Image.fromarray(z.astype(np.int32) + 1).resize((zw, zh), Image.NEAREST)) - 1
    arr = np.asarray(im).astype(float)
    for i, name in enumerate(DIV_NAMES):
        hexc = config.DIVISIONS.get(name, {"color": config.EXTRA_COLOR})["color"].lstrip("#")
        rgb = np.array([int(hexc[k:k + 2], 16) for k in (0, 2, 4)])
        a = 0.3 if name in config.DIVISIONS else 0.2
        arr[z == i] = arr[z == i] * (1 - a) + rgb * a
    out = Image.fromarray(arr.astype(np.uint8))
    d = ImageDraw.Draw(out)
    f = _font(14, bold=True)
    for i, name in enumerate(DIV_NAMES):
        ys, xs = np.nonzero(z == i)
        if len(xs):
            d.text((int(xs.mean()) - 25, int(ys.mean())), name, fill=(0, 0, 0), font=f,
                   stroke_width=2, stroke_fill=(255, 255, 255))
    return out
