"""সেটিংস / Settings for the Dhaka traffic collector (v4: 761 km² rectangle, zoom 18, every 15 min, 30 days, 12 shards)."""
import math

# ---------------- সময় / Timing ----------------
INTERVAL_SECONDS = 900          # 15 মিনিট পরপর একটি স্ন্যাপশট চক্র (দিনে 96টি)
COLLECTION_DAYS = 30            # 30 দিন পর স্বয়ংক্রিয়ভাবে বন্ধ হবে
RUN_MINUTES = 340               # প্রতিটি GitHub রান ~5 ঘণ্টা 40 মিনিট চলে, তারপর নিজেই পরের রান চালু করে
TIMEZONE = "Asia/Dhaka"

# ---------------- মানচিত্র / Map view ----------------
ZOOM = 18                       # বিশ্লেষণের জুম: প্রতি পিক্সেল ≈ 0.55 মিটার (গলি পর্যন্ত)
TILE = 3072                     # পুরো এলাকা এই মাপের টুকরো স্ক্রিনশটে তোলা হয় (জুম 18-এ প্রতি চক্রে 285টি)
SHARDS = 12                     # এতগুলো GitHub সার্ভার একসাথে কাজ ভাগ করে নেয়, যাতে 15 মিনিটের মধ্যে শেষ হয়
LABEL_ZOOM = 14                 # বিভাগ/থানার সীমানা-মানচিত্রের রেজোলিউশন (≈8.7 মিটার)
OVERVIEW_ZOOM = 15              # Drive-এ রাখার জন্য পুরো এলাকার একটি ছবি এই জুমে তোলা হয় (জুম 18-এর পূর্ণ ছবি ~1–2 GB হতো)
STUDY_VERSION = 4               # এই সংখ্যা বদলালে পুরোনো তথ্য data/old_vN ফোল্ডারে সরে যায় এবং ৩০ দিন নতুন করে শুরু হয়
PAGE_WAIT_FIRST = 15000         # প্রথম পেজ লোডের পর অপেক্ষা (মিলিসেকেন্ড)
PAGE_WAIT = 7000                # পরের প্রতিটি টুকরোর জন্য অপেক্ষা

# ---------------- হটস্পট (ঐচ্ছিক) ----------------
# পুরো এলাকাই এখন জুম 18-এ মাপা হয়, তাই আলাদা হটস্পট দরকার নেই। চাইলে এখানে যোগ করা যায়:
# HOTSPOTS = {"Farmgate": {"bn": "ফার্মগেট", "at": (23.7577, 90.3897)}}
HOTSPOT_ZOOM = 18
HOTSPOT_SIZE = 2048
HOTSPOT_IMAGE_EVERY = 4
HOTSPOTS = {}

# Drive-এ মোট জায়গার সর্বোচ্চ সীমা। প্রতিটি ছবি দরকার হলে স্বয়ংক্রিয়ভাবে ছোট করা হয় যাতে এই সীমা পার না হয়।
STORAGE_LIMIT_GB = 13.5               # 14 GB-এর নিচে থাকতে কিছুটা নিরাপত্তা-ফাঁক

# google.com/maps-এর সার্চ বক্স, বাটন ও কার্ড যেন মানচিত্র না ঢাকে, তাই চারপাশে ফাঁকা জায়গা রেখে পরে কেটে ফেলা হয়
UI_MARGIN = {"left": 110, "top": 210, "right": 60, "bottom": 150}
BOUNDS = {"north": 23.9501, "south": 23.6641, "west": 90.3103, "east": 90.5458}

# গবেষণা এলাকা: আয়তক্ষেত্র, ব্যবহারকারীর স্ক্রিনশটের হুবহু সীমানা ও অনুপাত (972:1290 = 0.7535)।
# উত্তরে বোর্ড বাজার (গাজীপুর), দক্ষিণে কেরানীগঞ্জ ও সিদ্ধিরগঞ্জ, পশ্চিমে আশুলিয়া-বিরুলিয়া, পূর্বে রূপগঞ্জ। ≈ 24.0 × 31.7 কিমি = 761 বর্গকিমি।
# Study area: rectangle matching the user's screenshot exactly (lat, lng corners).
STUDY_POLYGON = [
    (23.9501, 90.3103), (23.9501, 90.5458), (23.6641, 90.5458), (23.6641, 90.3103),
]

# ---------------- আবহাওয়া ও AI ----------------
WEATHER_LAT, WEATHER_LNG = 23.8103, 90.4125
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


# study frame at ZOOM (analysis), starting at the north-west corner
FRAME_X0, FRAME_Y0 = project(BOUNDS["north"], BOUNDS["west"])
_fx1, _fy1 = project(BOUNDS["south"], BOUNDS["east"])
VIEW_W, VIEW_H = int(math.ceil(_fx1 - FRAME_X0)), int(math.ceil(_fy1 - FRAME_Y0))
TILES_X, TILES_Y = math.ceil(VIEW_W / TILE), math.ceil(VIEW_H / TILE)
GRID_W, GRID_H = TILES_X * TILE, TILES_Y * TILE          # tile grid covering the frame

# label grid (zones/thanas) at LABEL_ZOOM: one label pixel = LABEL_FACTOR analysis pixels
LABEL_FACTOR = 2 ** (ZOOM - LABEL_ZOOM)
assert TILE % LABEL_FACTOR == 0, "TILE must be divisible by 2**(ZOOM-LABEL_ZOOM)"
LAB_W, LAB_H = GRID_W // LABEL_FACTOR, GRID_H // LABEL_FACTOR
OVERVIEW_SCALE = 1 / LABEL_FACTOR                         # kept for older helpers
OV_W, OV_H = LAB_W, LAB_H

# overview picture for Drive at OVERVIEW_ZOOM
OVF_X0, OVF_Y0 = project(BOUNDS["north"], BOUNDS["west"], OVERVIEW_ZOOM)
_ox1, _oy1 = project(BOUNDS["south"], BOUNDS["east"], OVERVIEW_ZOOM)
OVF_W, OVF_H = int(math.ceil(_ox1 - OVF_X0)), int(math.ceil(_oy1 - OVF_Y0))
OVF_TILES_X, OVF_TILES_Y = math.ceil(OVF_W / TILE), math.ceil(OVF_H / TILE)


def latlng_to_px(lat, lng):
    """Pixel position inside the full-resolution tile grid (zoom ZOOM)."""
    x, y = project(lat, lng)
    return x - FRAME_X0, y - FRAME_Y0


def latlng_to_ov(lat, lng):
    """Pixel position in the label grid (zoom LABEL_ZOOM)."""
    x, y = latlng_to_px(lat, lng)
    return x / LABEL_FACTOR, y / LABEL_FACTOR


def latlng_to_overview(lat, lng):
    """Pixel position in the Drive overview picture (zoom OVERVIEW_ZOOM)."""
    x, y = project(lat, lng, OVERVIEW_ZOOM)
    return x - OVF_X0, y - OVF_Y0


def browser_center(world_cx, world_cy, zoom, margins):
    """Centre to request so that the un-covered (clipped) part of the window is centred on (world_cx, world_cy)."""
    return unproject(world_cx + (margins["right"] - margins["left"]) / 2,
                     world_cy + (margins["bottom"] - margins["top"]) / 2, zoom)


def metres_per_px(lat, zoom=None):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** (ZOOM if zoom is None else zoom))
