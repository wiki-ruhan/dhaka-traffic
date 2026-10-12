"""সেটিংস / Settings for the Dhaka traffic collector.
v6: zoom 17 full-resolution images (optimized WebP) with an English footer (timestamp to the second, weather, AQI, wind),
snapshots every 15 min by day and every hour from 01:00 to 05:00, 30 days, no storage cap.
"""
import math
from datetime import datetime, timedelta

# ---------------- সময়সূচি / Schedule (Asia/Dhaka) ----------------
TIMEZONE = "Asia/Dhaka"
COLLECTION_DAYS = 30            # 30 দিন পর স্বয়ংক্রিয়ভাবে বন্ধ হবে
DAY_INTERVAL_MIN = 15           # দিনে প্রতি 15 মিনিটে
NIGHT_START_HOUR = 1            # রাত 1টা থেকে
NIGHT_END_HOUR = 5              # ভোর 5টা পর্যন্ত
NIGHT_INTERVAL_MIN = 60         # প্রতি ঘণ্টায় একটি (01:00, 02:00, 03:00, 04:00)
RUN_MINUTES = 340               # প্রতিটি GitHub রান ~5 ঘণ্টা 40 মিনিট চলে, তারপর পরের রান দায়িত্ব নেয়
LATE_GRACE_SECONDS = 120        # কোনো স্লট এর চেয়ে কম দেরিতে ধরা পড়লে তখনই তোলা হয়

# ---------------- এলাকা / Study area (rectangle) ----------------
# ব্যবহারকারীর সর্বশেষ স্ক্রিনশটের হুবহু সীমানা ও অনুপাত (1568:2353 = 0.6664): উত্তরে টঙ্গী-আশুলিয়া, দক্ষিণে কেরানীগঞ্জ,
# পশ্চিমে আমিনবাজার-বিরুলিয়া, পূর্বে বসুন্ধরা-বাড্ডা। ≈ 17.8 × 26.7 কিমি = 475 বর্গকিমি।
# `python runner.py --plan` prints the size, tiles, runtime and storage for these bounds.
BOUNDS = {"north": 23.9090, "south": 23.6688, "west": 90.2946, "east": 90.4696}
STUDY_POLYGON = [
    (BOUNDS["north"], BOUNDS["west"]), (BOUNDS["north"], BOUNDS["east"]),
    (BOUNDS["south"], BOUNDS["east"]), (BOUNDS["south"], BOUNDS["west"]),
]

# ---------------- মানচিত্র / Map ----------------
ZOOM = 17                       # প্রতি পিক্সেল ≈ 1.1 মিটার: সংযোগ সড়ক ও পাড়ার রাস্তার যানজট দেখা যায়
TILE = 3072                     # এলাকা এই মাপের টুকরোয় তোলা হয়, তারপর জোড়া লাগানো হয়
LABEL_ZOOM = 14                 # বিভাগ/থানার সীমানা-মানচিত্রের রেজোলিউশন
TILES_PER_RUNNER = 50           # একটি সার্ভার এক চক্রে এর বেশি টুকরো তুলবে না; জুম 17-এ পুরো এলাকা (48 টুকরো) একটি সার্ভারেই হয় → একটি পূর্ণ ছবি
STUDY_VERSION = 6               # এই সংখ্যা বদলালে পুরোনো তথ্য data/old_vN-এ সরে যায় এবং ৩০ দিন নতুন করে শুরু হয়
PAGE_WAIT_FIRST = 15000         # প্রথম পেজ লোডের পর অপেক্ষা (মিলিসেকেন্ড)
PAGE_WAIT = 7000                # পরের প্রতিটি টুকরোর জন্য অপেক্ষা
# google.com/maps-এর সার্চ বক্স, বাটন ও কার্ড যেন মানচিত্র না ঢাকে, তাই চারপাশে ফাঁকা জায়গা রেখে পরে কেটে ফেলা হয়
UI_MARGIN = {"left": 110, "top": 210, "right": 60, "bottom": 150}

# ---------------- ছবি / Saved images ----------------
# ছবির ফরম্যাট: "webp" = সবচেয়ে ছোট ফাইল (JPEG-এর চেয়ে ~৩৫% ছোট, পূর্ণ রেজোলিউশন) | "jpg" | "png" (কোনো মান হারায় না, অনেক বড়)
# WebP-তে প্রতি পাশে সর্বোচ্চ 16,383 পিক্সেল; এর বেশি হলে স্বয়ংক্রিয়ভাবে JPEG হয়।
IMAGE_FORMAT = "webp"
WEBP_QUALITY = 80               # 0–100
WEBP_METHOD = 4                 # 0–6: বেশি = সামান্য ছোট কিন্তু ধীর
MAX_WEBP_MP = 120               # WebP একটি ফাইলে ~150 মেগাপিক্সেলের বেশি নিতে পারে না, তাই ছবি ফালিতে ভাগ হয়
JPG_QUALITY = 85
PNG_COMPRESS_LEVEL = 4          # 0–9: বেশি = ছোট ফাইল কিন্তু ধীর
DRIVE_ROOT = "Dhaka Traffic Snapshots"   # Google Drive-এ মূল ফোল্ডারের নাম
UPLOAD_CHUNK_MB = 32            # বড় ফাইল এই মাপের টুকরোয় Drive-এ পাঠানো হয়

# ---------------- আবহাওয়া, AQI ও AI ----------------
# আবহাওয়া ও বাতাসের মান নেওয়া হয় এলাকার কেন্দ্র থেকে (Open-Meteo, বিনামূল্যে, কোনো key লাগে না)
WEATHER_LAT, WEATHER_LNG = None, None   # None = এলাকার কেন্দ্র
AI_MODEL = "claude-haiku-5-5"   # শুধু ANTHROPIC_API_KEY থাকলে ব্যবহার হয় (ঐচ্ছিক)
WEEKEND_DAYS = {4, 5}           # শুক্র ও শনিবার

# ---------------- DMP ট্রাফিক বিভাগ ও থানা ----------------
# থানার অবস্থান আনুমানিক কেন্দ্রবিন্দু। প্রতিটি পিক্সেল তার নিকটতম থানার বিভাগে যায় (Voronoi)।
# Approximate thana centre points; verify against official DMP maps and edit if needed.
DIVISIONS = {
    "Ramna":    {"bn": "রমনা",    "color": "#6C5CE7"},
    "Lalbagh":  {"bn": "লালবাগ",  "color": "#B8860B"},
    "Motijheel": {"bn": "মতিঝিল", "color": "#0984E3"},
    "Wari":     {"bn": "ওয়ারী",    "color": "#00897B"},
    "Tejgaon":  {"bn": "তেজগাঁও",  "color": "#D63384"},
    "Mirpur":   {"bn": "মিরপুর",  "color": "#E17055"},
    "Gulshan":  {"bn": "গুলশান",  "color": "#2D9CDB"},
    "Uttara":   {"bn": "উত্তরা",   "color": "#5E8C31"},
}

# DMP-এর বাইরের এলাকা (নিজস্ব অঞ্চল হিসেবে আলাদা স্কোর পাবে) / Areas outside DMP, each scored separately
EXTRA_AREAS = {
    "Gazipur-Tongi": {"bn": "টঙ্গী–গাজীপুর",      "pts": [(23.9060, 90.4050), (23.9400, 90.3850), (23.9300, 90.4300)]},
    "Ashulia":       {"bn": "আশুলিয়া",           "pts": [(23.9020, 90.3300), (23.9350, 90.3300)]},
    "Birulia":       {"bn": "বিরুলিয়া",          "pts": [(23.8480, 90.3330)]},
    "Aminbazar":     {"bn": "আমিনবাজার",         "pts": [(23.7880, 90.3230)]},
    "Purbachal":     {"bn": "পূর্বাচল",           "pts": [(23.8520, 90.4950), (23.8350, 90.5250), (23.8800, 90.5100)]},
    "Rupganj-Tarabo": {"bn": "রূপগঞ্জ–তারাবো",   "pts": [(23.7400, 90.5200), (23.7750, 90.5180), (23.8050, 90.5350)]},
    "Kanchpur-Siddhirganj": {"bn": "কাঁচপুর–সিদ্ধিরগঞ্জ", "pts": [(23.7080, 90.5150), (23.6850, 90.4990)]},
    "Keraniganj":    {"bn": "কেরানীগঞ্জ",         "pts": [(23.6950, 90.3850), (23.7000, 90.4050), (23.7350, 90.3380), (23.6750, 90.3400), (23.6750, 90.4200)]},
}
EXTRA_COLOR = "#7F8C8D"

THANAS = {
    # name: (lat, lng, division)
    "Ramna Model": (23.7440, 90.4040, "Ramna"), "Dhanmondi": (23.7465, 90.3760, "Ramna"),
    "Shahbag": (23.7385, 90.3955, "Ramna"), "New Market": (23.7330, 90.3850, "Ramna"),
    "Kalabagan": (23.7505, 90.3830, "Ramna"),
    "Lalbagh": (23.7190, 90.3880, "Lalbagh"), "Kotwali": (23.7085, 90.4060, "Lalbagh"),
    "Chawkbazar": (23.7160, 90.3970, "Lalbagh"), "Bangshal": (23.7190, 90.4080, "Lalbagh"),
    "Hazaribagh": (23.7330, 90.3650, "Lalbagh"), "Kamrangirchar": (23.7140, 90.3700, "Lalbagh"),
    "Motijheel": (23.7310, 90.4180, "Motijheel"), "Paltan Model": (23.7365, 90.4115, "Motijheel"),
    "Shahjahanpur": (23.7420, 90.4230, "Motijheel"), "Sabujbagh": (23.7385, 90.4380, "Motijheel"),
    "Khilgaon": (23.7510, 90.4300, "Motijheel"), "Mugda": (23.7290, 90.4300, "Motijheel"),
    "Rampura": (23.7610, 90.4210, "Motijheel"),
    "Wari": (23.7180, 90.4200, "Wari"), "Sutrapur": (23.7090, 90.4170, "Wari"),
    "Gendaria": (23.7030, 90.4260, "Wari"), "Jatrabari": (23.7100, 90.4350, "Wari"),
    "Kadamtali": (23.6950, 90.4430, "Wari"), "Shyampur": (23.6870, 90.4300, "Wari"),
    "Demra": (23.7230, 90.4900, "Wari"),
    "Tejgaon": (23.7600, 90.3930, "Tejgaon"), "Tejgaon I/A": (23.7700, 90.4000, "Tejgaon"),
    "Sher-e-Bangla Nagar": (23.7720, 90.3770, "Tejgaon"), "Mohammadpur": (23.7620, 90.3580, "Tejgaon"),
    "Adabor": (23.7740, 90.3540, "Tejgaon"), "Hatirjheel": (23.7530, 90.4060, "Tejgaon"),
    "Mirpur Model": (23.7990, 90.3650, "Mirpur"), "Pallabi": (23.8240, 90.3640, "Mirpur"),
    "Kafrul": (23.7900, 90.3850, "Mirpur"), "Shah Ali": (23.8070, 90.3500, "Mirpur"),
    "Rupnagar": (23.8180, 90.3500, "Mirpur"), "Darussalam": (23.7850, 90.3500, "Mirpur"),
    "Bhashantek": (23.8170, 90.3880, "Mirpur"), "Cantonment": (23.8100, 90.4000, "Mirpur"),
    "Gulshan": (23.7900, 90.4150, "Gulshan"), "Banani": (23.7940, 90.4040, "Gulshan"),
    "Badda": (23.7800, 90.4260, "Gulshan"), "Vatara": (23.8050, 90.4300, "Gulshan"),
    "Khilkhet": (23.8300, 90.4200, "Gulshan"),
    "Uttara East": (23.8700, 90.4000, "Uttara"), "Uttara West": (23.8750, 90.3850, "Uttara"),
    "Airport": (23.8500, 90.4050, "Uttara"), "Turag": (23.8800, 90.3650, "Uttara"),
    "Dakshinkhan": (23.8600, 90.4300, "Uttara"), "Uttarkhan": (23.8800, 90.4350, "Uttara"),
}


# ---------------- Web Mercator helpers ----------------
def project(lat, lng, zoom=None):
    """World pixel position of a lat/lng at a zoom level."""
    world = 256 * 2 ** (ZOOM if zoom is None else zoom)
    x = (lng + 180.0) / 360.0 * world
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * world
    return x, y


def unproject(x, y, zoom=None):
    world = 256 * 2 ** (ZOOM if zoom is None else zoom)
    lng = x / world * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / world))))
    return lat, lng


# study frame at ZOOM, starting at the north-west corner
FRAME_X0, FRAME_Y0 = project(BOUNDS["north"], BOUNDS["west"])
_fx1, _fy1 = project(BOUNDS["south"], BOUNDS["east"])
VIEW_W, VIEW_H = int(math.ceil(_fx1 - FRAME_X0)), int(math.ceil(_fy1 - FRAME_Y0))
TILES_X, TILES_Y = math.ceil(VIEW_W / TILE), math.ceil(VIEW_H / TILE)
GRID_W, GRID_H = TILES_X * TILE, TILES_Y * TILE

# parallel runners: each one takes a band of whole tile rows and saves it as one image ("strip")
SHARDS = max(1, min(TILES_Y, math.ceil(TILES_X * TILES_Y / TILES_PER_RUNNER)))
if IMAGE_FORMAT.lower() == "webp":  # one WebP file must stay below ~150 megapixels
    _max_rows = max(1, int(MAX_WEBP_MP * 1e6 // (VIEW_W * TILE)))
    SHARDS = max(SHARDS, math.ceil(TILES_Y / _max_rows))
ROWS_PER_SHARD = math.ceil(TILES_Y / SHARDS)
SHARDS = math.ceil(TILES_Y / ROWS_PER_SHARD)

# label grid (zones/thanas) at LABEL_ZOOM
LABEL_FACTOR = 2 ** (ZOOM - LABEL_ZOOM)
assert TILE % LABEL_FACTOR == 0, "TILE must be divisible by 2**(ZOOM-LABEL_ZOOM)"
LAB_W, LAB_H = GRID_W // LABEL_FACTOR, GRID_H // LABEL_FACTOR
OVERVIEW_SCALE = 1 / LABEL_FACTOR
OV_W, OV_H = LAB_W, LAB_H
CENTER_LAT = (BOUNDS["north"] + BOUNDS["south"]) / 2
CENTER_LNG = (BOUNDS["west"] + BOUNDS["east"]) / 2


def latlng_to_px(lat, lng):
    x, y = project(lat, lng)
    return x - FRAME_X0, y - FRAME_Y0


def latlng_to_ov(lat, lng):
    x, y = latlng_to_px(lat, lng)
    return x / LABEL_FACTOR, y / LABEL_FACTOR


def browser_center(world_cx, world_cy, zoom, margins):
    """Centre to request so that the un-covered (clipped) part of the window is centred on (world_cx, world_cy)."""
    return unproject(world_cx + (margins["right"] - margins["left"]) / 2,
                     world_cy + (margins["bottom"] - margins["top"]) / 2, zoom)


def metres_per_px(lat, zoom=None):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** (ZOOM if zoom is None else zoom))


def area_km2():
    ns = (BOUNDS["north"] - BOUNDS["south"]) * 111.0
    ew = (BOUNDS["east"] - BOUNDS["west"]) * 111.32 * math.cos(math.radians(CENTER_LAT))
    return ns, ew, ns * ew


# ---------------- Schedule helpers ----------------
def slots_for_day(day):
    """All snapshot times on one calendar day (naive local times): hourly 01:00–04:00, otherwise every 15 min."""
    out = []
    for h in range(24):
        if NIGHT_START_HOUR <= h < NIGHT_END_HOUR:
            mins = range(0, 60, NIGHT_INTERVAL_MIN)
        else:
            mins = range(0, 60, DAY_INTERVAL_MIN)
        out += [datetime(day.year, day.month, day.day, h, m) for m in mins]
    return out


SLOTS_PER_DAY = len(slots_for_day(datetime(2026, 1, 1)))
TOTAL_SLOTS = SLOTS_PER_DAY * COLLECTION_DAYS


def next_slot(t):
    """First snapshot time strictly after t (t is timezone-aware)."""
    base = t.replace(tzinfo=None)
    for d in (0, 1, 2):
        for s in slots_for_day(base + timedelta(days=d)):
            if s > base:
                return s.replace(tzinfo=t.tzinfo)


def prev_slot(t):
    """Last snapshot time at or before t."""
    base = t.replace(tzinfo=None)
    for d in (0, 1):
        day_slots = [s for s in slots_for_day(base - timedelta(days=d)) if s <= base]
        if day_slots:
            return day_slots[-1].replace(tzinfo=t.tzinfo)


def slot_id(t):
    return t.strftime("%Y%m%d-%H%M")
