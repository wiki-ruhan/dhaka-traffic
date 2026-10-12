"""Builds the daily summary, tags likely reasons for each day, and writes the dashboard (docs/index.html).

Reasons come from: weather (Open-Meteo), weekend/holiday calendar, and news headlines (Google News RSS).
If ANTHROPIC_API_KEY is set, an AI model also writes a short plain-language explanation per day.
"""
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import config

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
NEWS_DIR = DATA / "news"
AI_DIR = DATA / "ai"
TZ = ZoneInfo(config.TIMEZONE)
NOW = datetime.now(TZ)
TODAY = NOW.date()
CITY = "Study area (all)"
TOTALS = {CITY, "DMP (all)"}
ZONE_BN = {**{k: v["bn"] for k, v in config.DIVISIONS.items()},
           **{k: v["bn"] for k, v in config.EXTRA_AREAS.items()}}
UA = {"User-Agent": "Mozilla/5.0 (research project: Dhaka traffic log)"}

NEWS_TAGS = {
    "Hartal / blockade": [r"\bhartal\b", r"\bblockade", r"\bshutdown\b", "হরতাল", "অবরোধ"],
    "Rally / protest": [r"\brall(y|ies)\b", r"\bprotest", r"\bprocession", r"\bdemonstrat", r"\bsit-in\b",
                        "মিছিল", "সমাবেশ", "বিক্ষোভ", "মানববন্ধন"],
    "Accident": [r"\baccident", r"\bcollision", r"\bcrash", "দুর্ঘটনা"],
    "Waterlogging": [r"\bwaterlog", r"\binundat", r"\bflooded\b", "জলাবদ্ধতা", "পানিবন্দি"],
    "VIP movement": [r"\bvip\b", r"\bmotorcade\b"],
    "Roadwork / construction": [r"\broad ?work", r"\bdigging\b", r"\brepair work", r"\bconstruction work",
                                "খোঁড়াখুঁড়ি", "সংস্কার কাজ"],
    "Fire / emergency": [r"\bfire\b", r"\bblaze\b", "আগুন", "অগ্নিকাণ্ড"],
    "Festival / holiday travel": [r"\beid\b", r"\bpuja\b", r"\bholiday rush", "ঈদ", "পূজা"],
    "Transport strike / disruption": [r"\btransport strike", r"\bbus strike", r"\bmetro (rail )?(service )?(suspend|halt|disrupt)",
                                      "ধর্মঘট"],
}


# ---------------------------------------------------------------- inputs
def load_hourly():
    f = DATA / "snapshots.csv"
    if not f.exists():
        return pd.DataFrame()
    df = pd.read_csv(f)
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df["date"] = df["date"].astype(str)
    df["hour"] = df["hour"].astype(int)
    if "quality" in df:
        df = df[df["quality"] == "ok"]
    return df


def load_weather(dates):
    """Hourly precipitation (mm) for the collection window, cached in data/weather_hourly.csv."""
    cache = DATA / "weather_hourly.csv"
    old = pd.read_csv(cache, dtype={"date": str}) if cache.exists() else pd.DataFrame(columns=["date", "hour", "rain_mm"])
    if dates:
        past_days = min(92, (TODAY - date.fromisoformat(min(dates))).days + 1)
        url = ("https://api.open-meteo.com/v1/forecast"
               f"?latitude={config.WEATHER_LAT}&longitude={config.WEATHER_LNG}"
               f"&hourly=precipitation&past_days={past_days}&forecast_days=1&timezone={quote(config.TIMEZONE)}")
        try:
            js = requests.get(url, timeout=30).json()["hourly"]
            new = pd.DataFrame({"time": js["time"], "rain_mm": js["precipitation"]})
            new["date"] = new["time"].str[:10]
            new["hour"] = new["time"].str[11:13].astype(int)
            new = new[new["date"] <= TODAY.isoformat()][["date", "hour", "rain_mm"]]
            old = pd.concat([old, new]).drop_duplicates(["date", "hour"], keep="last")
            old.to_csv(cache, index=False)
        except Exception as e:
            print(f"Weather fetch skipped: {type(e).__name__}")
    old["hour"] = old["hour"].astype(int)
    return old


def holiday_lookup(years):
    try:
        import holidays
        return holidays.country_holidays("BD", years=years)
    except Exception:
        return {}


def fetch_rss(query, lang):
    gl = "BD"
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl={lang}-{gl}&gl={gl}&ceid={gl}:{lang}"
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    items = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        src = it.find("source")
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(TZ).date().isoformat()
        except Exception:
            pub = None
        items.append({"title": title, "source": src.text if src is not None else "",
                      "link": it.findtext("link") or "", "date": pub})
    return items


def get_news(day):
    """Headlines about Dhaka traffic disruptions for one day, cached per day."""
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    f = NEWS_DIR / f"{day}.json"
    d = date.fromisoformat(day)
    if f.exists():
        cached = json.loads(f.read_text())
        age_h = (NOW - datetime.fromisoformat(cached["fetched_at"])).total_seconds() / 3600
        recent_day = (TODAY - d).days <= 1
        if not recent_day or age_h < 3:
            return cached
    nxt = (d + timedelta(days=1)).isoformat()
    queries = [
        (f"Dhaka (traffic OR gridlock OR hartal OR blockade OR rally OR protest OR waterlogging OR accident OR VIP) after:{day} before:{nxt}", "en"),
        (f"ঢাকা (যানজট OR অবরোধ OR হরতাল OR মিছিল OR জলাবদ্ধতা OR দুর্ঘটনা) after:{day} before:{nxt}", "bn"),
    ]
    heads, seen, failed = [], set(), False
    for q, lang in queries:
        try:
            for it in fetch_rss(q, lang):
                if it["date"] and it["date"] != day:
                    continue
                key = it["title"].lower()
                if key not in seen:
                    seen.add(key)
                    heads.append(it)
        except Exception as e:
            failed = True
            print(f"News fetch skipped for {day} ({lang}): {type(e).__name__}")
    tags = sorted({tag for h in heads for tag, pats in NEWS_TAGS.items()
                   if any(re.search(p, h["title"], re.IGNORECASE) for p in pats)})
    out = {"fetched_at": NOW.isoformat(), "headlines": heads[:25], "tags": tags}
    if not failed:  # only cache complete results, so a failed day is retried next time
        f.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def ai_summary(day_row, headlines, budget):
    """Optional 2–3 sentence explanation from Claude. Cached; only for finished days."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    AI_DIR.mkdir(parents=True, exist_ok=True)
    f = AI_DIR / f"{day_row['date']}.txt"
    if f.exists():
        return f.read_text().strip(), budget
    if not key or budget <= 0 or date.fromisoformat(day_row["date"]) >= TODAY:
        return None, budget
    facts = {k: day_row[k] for k in ("date", "weekday", "day_type", "city_avg", "baseline", "level",
                                      "peak_hour", "worst_zone", "rain_mm", "rain_effect", "tags")}
    prompt = (
        "You are helping a traffic researcher in Dhaka, Bangladesh. Below are one day's congestion statistics "
        "for greater Dhaka (DMP area plus Ashulia, Tongi, Purbachal, Kanchpur, Narayanganj, Keraniganj) (score 0-100 from Google Maps traffic colours, higher = worse) "
        "and that day's news headlines. "
        "In 2-3 plain sentences written in Bangla, explain the most likely reasons for the day's traffic. "
        "Only use the evidence given; say 'সম্ভবত' where unsure and say so if the evidence does not explain it. No preamble.\n\n"
        f"STATS: {json.dumps(facts, ensure_ascii=False)}\n\nHEADLINES:\n" +
        "\n".join(f"- {h['title']}" for h in headlines[:15])
    )
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": config.AI_MODEL, "max_tokens": 300,
                  "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json()["content"]).strip()
        f.write_text(text)
        return text, budget - 1
    except Exception as e:
        print(f"AI summary skipped for {day_row['date']}: {e}")
        return None, budget - 1


# ---------------------------------------------------------------- analysis
WEEKDAY_BN = ["সোমবার", "মঙ্গলবার", "বুধবার", "বৃহস্পতিবার", "শুক্রবার", "শনিবার", "রবিবার"]
DAYTYPE_BN = {"Workday": "কর্মদিবস", "Weekend": "সাপ্তাহিক ছুটি", "Holiday": "সরকারি ছুটি"}
TAG_BN = {
    "Hartal / blockade": "হরতাল / অবরোধ", "Rally / protest": "মিছিল / সমাবেশ / বিক্ষোভ", "Accident": "দুর্ঘটনা",
    "Waterlogging": "জলাবদ্ধতা", "VIP movement": "ভিআইপি চলাচল", "Roadwork / construction": "রাস্তা খোঁড়াখুঁড়ি / নির্মাণ",
    "Fire / emergency": "অগ্নিকাণ্ড / জরুরি অবস্থা", "Festival / holiday travel": "উৎসব / ছুটির যাত্রা",
    "Transport strike / disruption": "পরিবহন ধর্মঘট / বিঘ্ন",
}


def level_for(ratio):
    if ratio is None:
        return "na", "তুলনার জন্য এখনো যথেষ্ট দিন হয়নি"
    if ratio >= 1.2:
        return "much_heavier", "স্বাভাবিকের চেয়ে অনেক বেশি যানজট"
    if ratio >= 1.07:
        return "heavier", "স্বাভাবিকের চেয়ে বেশি যানজট"
    if ratio <= 0.8:
        return "much_lighter", "স্বাভাবিকের চেয়ে অনেক কম যানজট"
    if ratio <= 0.93:
        return "lighter", "স্বাভাবিকের চেয়ে কম যানজট"
    return "typical", "স্বাভাবিক"


def build():
    df = load_hourly()
    if df.empty:
        print("No data yet.")
        write_dashboard({"empty": True, "updated": NOW.strftime("%d %b %Y, %H:%M")})
        return

    city = df[df["zone"] == CITY].groupby(["date", "hour"], as_index=False)["score"].mean()
    zones = df[~df["zone"].isin(TOTALS)]
    dates = sorted(city["date"].unique())
    weather = load_weather(dates)
    city = city.merge(weather, on=["date", "hour"], how="left")
    hol = holiday_lookup(sorted({int(d[:4]) for d in dates}))

    days = []
    for d in dates:
        c = city[city["date"] == d].dropna(subset=["score"])
        if c.empty:
            continue
        dd = date.fromisoformat(d)
        holiday = hol.get(dd) if hol else None
        weekend = dd.weekday() in config.WEEKEND_DAYS
        z = zones[zones["date"] == d].groupby("zone")["score"].mean().dropna()
        peak = c.loc[c["score"].idxmax()]
        rain_day = weather[weather["date"] == d]["rain_mm"].fillna(0)
        wet = c[c["rain_mm"].fillna(0) >= 0.5]
        dry = c[c["rain_mm"].fillna(0) < 0.5]
        rain_effect = round(wet["score"].mean() - dry["score"].mean(), 1) if len(wet) >= 2 and len(dry) >= 2 else None
        days.append({
            "date": d, "weekday": dd.strftime("%A"), "weekday_bn": WEEKDAY_BN[dd.weekday()],
            "day_type": "Holiday" if holiday else ("Weekend" if weekend else "Workday"),
            "day_type_bn": DAYTYPE_BN["Holiday" if holiday else ("Weekend" if weekend else "Workday")],
            "holiday": holiday, "hours": int(len(c)),
            "city_avg": round(float(c["score"].mean()), 1),
            "city_peak": round(float(peak["score"]), 1), "peak_hour": int(peak["hour"]),
            "worst_zone": z.idxmax() if len(z) else None,
            "worst_zone_score": round(float(z.max()), 1) if len(z) else None,
            "rain_mm": round(float(rain_day.sum()), 1), "rain_hours": int((rain_day >= 0.5).sum()),
            "rain_effect": rain_effect,
        })

    # baselines: typical score for the same kind of day (workday vs off-day), full days only
    full = [x for x in days if x["hours"] >= 18]
    for x in days:
        group = [y["city_avg"] for y in full if (y["day_type"] == "Workday") == (x["day_type"] == "Workday")]
        if len(group) < 3:
            group = [y["city_avg"] for y in full]
        base = float(pd.Series(group).median()) if len(group) >= 3 else None
        x["baseline"] = round(base, 1) if base else None
        x["level"], x["level_bn"] = level_for(x["city_avg"] / base if base else None)

    budget = 3  # max AI calls per run (backfills gradually)
    for x in days:
        news = get_news(x["date"])
        x["tags"] = news["tags"]
        x["headlines"] = news["headlines"][:6]
        reasons = []
        if x["holiday"]:
            reasons.append(f"সরকারি ছুটি: {x['holiday']}")
        elif x["day_type"] == "Weekend":
            reasons.append(f"সাপ্তাহিক ছুটি ({x['weekday_bn']})")
        if x["rain_mm"] >= 2:
            r = f"বৃষ্টি: {x['rain_mm']:.0f} মিমি, {x['rain_hours']} ঘণ্টা"
            if x["rain_effect"] is not None and x["rain_effect"] >= 5:
                r += f"; বৃষ্টির সময় যানজট {x['rain_effect']:.0f} পয়েন্ট বেশি"
            reasons.append(r)
        reasons += [f"সংবাদে: {TAG_BN.get(t, t)}" for t in x["tags"]]
        if x["level"] in ("heavier", "much_heavier") and not reasons:
            reasons.append("বাইরের কোনো কারণ পাওয়া যায়নি; সম্ভবত স্বাভাবিক কর্মদিবসের চাপ")
        if x["hours"] < 18:
            reasons.append(f"আংশিক দিন: {x['hours']}/24 ঘণ্টার তথ্য")
        x["reasons"] = reasons
        x["ai"], budget = ai_summary(x, news["headlines"], budget)

    # zone ranking over the whole period
    zone_rank = (zones.groupby("zone")["score"].mean().dropna().sort_values(ascending=False).round(1))
    zone_hour = (zones.groupby(["zone", "hour"])["score"].mean().round(1).unstack().reindex(columns=range(24)))

    heat = {d: [None] * 24 for d in dates}
    for _, r in city.iterrows():
        if pd.notna(r["score"]):
            heat[r["date"]][int(r["hour"])] = round(float(r["score"]), 1)

    first = datetime.fromisoformat(df["timestamp"].iloc[0])
    last = datetime.fromisoformat(df["timestamp"].iloc[-1])
    payload = {
        "empty": False,
        "updated": NOW.strftime("%d %b %Y, %H:%M"),
        "last_snapshot": last.strftime("%d %b %Y, %H:%M"),
        "day_number": min(config.COLLECTION_DAYS, (last.date() - first.date()).days + 1),
        "total_days": config.COLLECTION_DAYS,
        "snapshots": int(df[df["zone"] == CITY].shape[0]),
        "per_day": round(24 * 3600 / config.INTERVAL_SECONDS),
        "drive_ok": int((df[df["zone"] == CITY].get("drive", pd.Series(dtype=str)) == "ok").sum()),
        "div_meta": {k: {"bn": v} for k, v in ZONE_BN.items()},
        "mode": df["mode"].iloc[-1],
        "days": days,
        "heat": [{"date": d, "values": heat[d]} for d in dates],
        "zones": [{"zone": k, "bn": ZONE_BN.get(k, k), "dmp": k in config.DIVISIONS, "score": float(v)} for k, v in zone_rank.items()],
        "zone_hour": {k: [None if pd.isna(v) else float(v) for v in row] for k, row in zone_hour.iterrows()},
    }

    # tidy CSV for your paper / statistics software
    pd.DataFrame([{k: (", ".join(v) if isinstance(v, list) and k != "headlines" else v)
                   for k, v in x.items() if k != "headlines"} for x in days]).to_csv(DATA / "daily_summary.csv", index=False)
    write_dashboard(payload)
    print(f"Dashboard updated: {len(days)} days, {payload['snapshots']} snapshots.")


def write_dashboard(payload):
    DOCS.mkdir(exist_ok=True)
    tpl = (ROOT / "dashboard_template.html").read_text(encoding="utf-8")
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    (DOCS / "index.html").write_text(tpl.replace("/*__DATA__*/null", blob), encoding="utf-8")


if __name__ == "__main__":
    build()
