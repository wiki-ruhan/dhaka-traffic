"""সেটিংস / Settings for the Dhaka (DMP) traffic collector."""
import math

# ---------------- সময় / Timing ----------------
INTERVAL_SECONDS = 576          # 9.6 মিনিট পরপর একটি স্ন্যাপশট (দিনে 150টি)
COLLECTION_DAYS = 30            # 30 দিন পর স্বয়ংক্রিয়ভাবে বন্ধ হবে
RUN_MINUTES = 340               # প্রতিটি GitHub রান ~5 ঘণ্টা 40 মিনিট চলে, তারপর নিজেই পরের রান চালু করে
TIMEZONE = "Asia/Dhaka"

# ---------------- মানচিত্র / Map view: full DMP area ----------------
ZOOM = 13
BOUNDS = {"north": 23.935, "south": 23.598, "west": 90.300, "east": 90.550}

# গবেষণা এলাকার সীমানা: ব্যবহারকারীর আঁকা লাল রেখা (আশুলিয়া–টঙ্গী–পূর্বাচল–কাঁচপুর–নারায়ণগঞ্জ–কেরানীগঞ্জ–আমিনবাজার)
# Study-area boundary traced from the user's map (lat, lng). Only pixels inside this polygon are counted.
STUDY_POLYGON = [
    (23.9070, 90.3075), (23.9214, 90.3247), (23.9249, 90.3622), (23.9186, 90.3966), (23.8987, 90.4216),
    (23.8702, 90.4309), (23.8518, 90.4403), (23.8503, 90.4872), (23.8589, 90.5216), (23.8418, 90.5372),
    (23.8162, 90.5403), (23.7735, 90.5309), (23.7480, 90.5184), (23.7167, 90.5091), (23.6740, 90.5091),
    (23.6313, 90.5044), (23.6143, 90.4903), (23.6057, 90.4591), (23.6171, 90.4247), (23.6484, 90.4091),
    (23.6712, 90.3903), (23.6882, 90.3622), (23.7110, 90.3372), (23.7366, 90.3247), (23.7508, 90.3169),
    (23.7878, 90.3294), (23.8304, 90.3247), (23.8589, 90.3247), (23.8759, 90.3153),
]

# ---------------- ছবি সংরক্ষণ / Image storage ----------------
DRIVE_IMAGE_FORMAT = "jpg"      # "jpg" (~0.5 MB/ছবি, 30 দিনে ~2.3 GB) অথবা "png" (~2.5 MB/ছবি, ~11 GB)
JPG_QUALITY = 90

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
    "Ashulia":     {"bn": "আশুলিয়া",            "pts": [(23.9020, 90.3300)]},
    "Birulia":     {"bn": "বিরুলিয়া",           "pts": [(23.8480, 90.3330)]},
    "Aminbazar":   {"bn": "আমিনবাজার",          "pts": [(23.7880, 90.3230)]},
    "Tongi":       {"bn": "টঙ্গী",              "pts": [(23.9060, 90.4050)]},
    "Purbachal":   {"bn": "পূর্বাচল",            "pts": [(23.8520, 90.4950), (23.8350, 90.5250)]},
    "Rupganj-Tarabo": {"bn": "রূপগঞ্জ–তারাবো",   "pts": [(23.7400, 90.5200), (23.7750, 90.5180)]},
    "Kanchpur-Siddhirganj": {"bn": "কাঁচপুর–সিদ্ধিরগঞ্জ", "pts": [(23.7080, 90.5150), (23.6850, 90.4990)]},
    "Narayanganj": {"bn": "নারায়ণগঞ্জ ও ফতুল্লা",  "pts": [(23.6230, 90.4990), (23.6450, 90.4780)]},
    "Keraniganj":  {"bn": "কেরানীগঞ্জ",          "pts": [(23.6950, 90.3850), (23.6550, 90.4100), (23.7000, 90.4050), (23.7350, 90.3380)]},
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
def _project(lat, lng):
    world = 256 * 2 ** ZOOM
    x = (lng + 180.0) / 360.0 * world
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * world
    return x, y


_x0, _y0 = _project(BOUNDS["north"], BOUNDS["west"])
_x1, _y1 = _project(BOUNDS["south"], BOUNDS["east"])
VIEW_W, VIEW_H = int(round(_x1 - _x0)), int(round(_y1 - _y0))
_cx, _cy = (_x0 + _x1) / 2, (_y0 + _y1) / 2
CENTER_LNG = _cx / (256 * 2 ** ZOOM) * 360 - 180
CENTER_LAT = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * _cy / (256 * 2 ** ZOOM)))))


def latlng_to_px(lat, lng):
    x, y = _project(lat, lng)
    return x - _cx + VIEW_W / 2, y - _cy + VIEW_H / 2


def metres_per_px(lat):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** ZOOM)
