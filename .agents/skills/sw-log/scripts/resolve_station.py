#!/usr/bin/env python3
"""Resolve (frequency, UTC time, band) captures to broadcast stations.

Pipeline stage 3 of the sw-log skill.

Input: <data-dir>/pending.json (records from scan_photos.py that the agent has
annotated with `frequency_khz` and `band` after reading the photos).

Databases used:
  * SW:  EiBi schedule (offline JSONL built by eibi.py) + short-wave.info scrape
  * AM:  MWLIST quick-and-easy (Europe area) + EiBi (international MW)
  * FM:  no schedule database (local stations) -> candidates left empty

Matches are filtered by capture time (UTC) and day-of-week where the source
provides them. Candidates are pre-ranked by distance from the configured
location; the agent still reviews the list (multiple stations can share a
frequency; propagation decides what is really audible in Paris).

Usage:
  python3 resolve_station.py [--input .sw-log/pending.json] [--data-dir .sw-log]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import html
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0"
SCRIPT_DIR = Path(__file__).resolve().parent


def log(msg):
    print(msg, file=sys.stderr)


def seems_black(rec):
    return bool(rec.get("black"))


def infer_band(rec):
    band = (rec.get("band") or "").strip().upper()
    f = rec.get("frequency_khz")
    if band in ("SW", "AM", "FM"):
        return band
    if not f:
        return None
    if f >= 60000:                      # 60-108 MHz shown as kHz
        return "FM"
    if 150 <= f <= 1710:                # long/MW
        return "AM"
    if f >= 2300:
        return "SW"
    return None


def fetch(url, encoding="utf-8", retries=3, delay=2.0, timeout=30):
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode(encoding, errors="replace")
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429:
                wait = delay * (attempt + 1)
                try:
                    ra = int(exc.headers.get("Retry-After", 0))
                    if ra > 0:
                        wait = max(wait, ra)
                except (TypeError, ValueError):
                    pass
                log(f"[resolve] HTTP 429 {url} — waiting {wait:.0f}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
                continue
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(delay)
                continue
            break
    if last is not None:
        log(f"[resolve] fetch failed {url}: {last}")
    return None


# ---------- transmitter cache -----------------------------------------------
# short-wave.info / MWLIST transmitter data (site lat/lon, power, azimuth)
# rarely changes, so cache scraped responses per frequency to avoid re-fetching
# (and to dodge HTTP 429 rate-limiting). Cache file: <data-dir>/transmitter-cache.json

def load_cache(data_dir):
    p = data_dir / "transmitter-cache.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception as exc:  # noqa: BLE001
            log(f"[resolve] cache unreadable ({exc}), starting fresh")
    return {"freqs": {}}


def save_cache(data_dir, cache):
    (data_dir / "transmitter-cache.json").write_text(
        json.dumps(cache, indent=2, ensure_ascii=False))


def fetch_cached(freq, cache, ttl, data_dir, delay=2.0):
    """Return short-wave.info HTML for a frequency, reusing a cached scrape
    while it is younger than `ttl` seconds (transmitter data changes slowly)."""
    freqs = cache.setdefault("freqs", {})
    key = str(int(freq))
    entry = freqs.get(key)
    if entry:
        try:
            fetched = dt.datetime.fromisoformat(entry["fetched_utc"])
            if (dt.datetime.now(dt.timezone.utc) - fetched).total_seconds() < ttl:
                return entry["html"]
        except (KeyError, ValueError):
            pass
    html = fetch(f"http://www.short-wave.info/index.php?freq={int(freq)}", delay=delay)
    if html:
        freqs[key] = {"fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "html": html}
    return html


# ---------- short-wave.info -------------------------------------------------

def parse_swi(doc, capture_dt, lat, lon):
    m = re.search(r'<table[^>]*id="output"[^>]*>(.*?)</table>', doc, re.S)
    if not m:
        return []
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip().replace("\u2009", "")
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 8 or not cells[0].isdigit():
            continue
        freq = float(cells[0])
        station, start, end, days, lang = cells[1], cells[2], cells[3], cells[4], cells[5]
        site = cells[8]
        site_lat = site_lon = None
        mm = re.search(r"Latitude:\s*([0-9.]+)°([0-9.]+)['′]?([NSEW])", site)
        mo = re.search(r"Longitude:\s*([0-9.]+)°([0-9.]+)['′]?([NSEW])", site)
        if mm and mo:
            site_lat = float(mm.group(1)) + float(mm.group(2)) / 60
            site_lon = float(mo.group(1)) + float(mo.group(2)) / 60
            if mm.group(3) in "SW":
                site_lat = -site_lat
            if mo.group(3) in "SW":
                site_lon = -site_lon
        if not time_in_range(start, end, capture_dt):
            continue
        if not days_match(days, capture_dt.weekday() + 1):
            continue
        dist = haversine(lat, lon, site_lat, site_lon) if site_lat is not None else None
        out.append({
            "db": "short-wave.info",
            "station": station,
            "frequency_khz": freq,
            "start_utc": start, "end_utc": end,
            "days_raw": days, "lang": lang,
            "power_kw": cells[6], "azimuth": cells[7],
            "site": site,
            "distance_km": round(dist, 1) if dist else None,
        })
    return out


# ---------- EiBi -------------------------------------------------------------

def eibi_match(capture_dt, freq_khz, tolerance, data_dir):
    jsonl = None
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "eibi.py"),
         "--data-dir", str(data_dir), "--date", capture_dt.isoformat()],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        log(f"[resolve] eibi failed: {proc.stderr.strip()}")
        return []
    p = data_dir / "eibi" / "schedule.jsonl"
    if not p.exists():
        return []
    hits = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        if abs(r["freq"] - freq_khz) > tolerance:
            continue
        if not time_in_range_min(r["start_min"], r["end_min"], capture_dt.hour * 60 + capture_dt.minute):
            continue
        if capture_dt.weekday() not in r["days"]:
            continue
        hits.append({
            "db": "eibi",
            "station": r["station"],
            "frequency_khz": r["freq"],
            "start_utc": f"{r['start_min'] // 60:02d}:{r['start_min'] % 60:02d}",
            "end_utc": f"{r['end_min'] // 60:02d}:{r['end_min'] % 60:02d}",
            "days_approx": r.get("days_approx"),
            "itus": r.get("itus"), "lang": r.get("lang"),
            "target": r.get("target", ""), "remarks": r.get("remarks", ""),
        })
    return hits


# ---------- MWLIST -----------------------------------------------------------

def mwlist_match(freq_khz, area=1):
    doc = fetch(f"https://www.mwlist.org/mwlist_quick_and_easy.php?area={area}&kHz={int(freq_khz)}",
                encoding="latin-1")
    if not doc:
        return []
    hits = []
    for tbl in re.findall(r"<table[^>]*>(.*?)</table>", doc, re.S):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.S)
        for r in rows:
            cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
            if len(cells) < 4 or not cells[0] or cells[0] == "Country":
                continue
            name_hours = cells[2] if len(cells) > 2 else ""
            station = name_hours
            hours = lang = ""
            m = re.match(r"^(.*?)\s+(\d{4}-\d{4}|24h|24\s?h|24 Stunden)(?:\s+(\S+))?$", name_hours)
            if m:
                station, hours, lang = m.group(1), m.group(2), m.group(3) or ""
            hits.append({
                "db": "mwlist",
                "frequency_khz": float(freq_khz),
                "country": cells[0],
                "station": station,
                "hours": hours,
                "lang": lang,
                "transmitter": cells[3] if len(cells) > 3 else "",
                "power_kw": cells[5] if len(cells) > 5 else "",
                "remarks": cells[7] if len(cells) > 7 else "",
            })
    return hits


# ---------- helpers ----------------------------------------------------------

def time_in_range(start, end, when):
    s = start.split(":")
    e = end.split(":")
    t = when.hour * 60 + when.minute
    a = int(s[0]) * 60 + int(s[1])
    b = int(e[0]) * 60 + int(e[1])
    return _in_range(a, b, t)


def time_in_range_min(a, b, t):
    return _in_range(a, b, t)


def _in_range(a, b, t):
    if b <= a:                      # crosses midnight
        return t >= a or t < b
    return a <= t < b


_WEEKDAYS = {1: "Mo", 2: "Tu", 3: "We", 4: "Th", 5: "Fr", 6: "Sa", 7: "Su"}


def days_match(raw, weekday):
    """weekday: 1=Mon..7=Sun. Accept '1234567', '.23456.', 'Mo-Sa', '1......'."""
    raw = raw.strip()
    if not raw:
        return True
    if re.fullmatch(r"[1-7.]+", raw):
        return str(weekday) in raw
    try:
        return _WEEKDAYS[weekday] in raw
    except KeyError:
        return True


def haversine(lat1, lon1, lat2, lon2):
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def rec_label(rec):
    name = rec.get("name", "?")
    d = rec.get("directory")
    if d:
        return os.path.join(os.path.basename(str(d)), name)
    return name


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default=".sw-log/pending.json")
    ap.add_argument("--data-dir", default=".sw-log")
    ap.add_argument("--sw-tolerance", type=float, default=0.5, help="kHz tolerance for EiBi freq match")
    ap.add_argument("--sw-delay", type=float, default=1.0,
                    help="seconds to wait before each short-wave.info query (avoid HTTP 429)")
    ap.add_argument("--sw-cache-ttl", type=float, default=604800,
                    help="seconds a cached short-wave.info scrape stays fresh (default 7d)")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).resolve()
    recs = json.loads(Path(args.input).read_text()).get("records", [])

    config = json.loads((data_dir / "config.json").read_text()) if (data_dir / "config.json").exists() else {}
    loc = config.get("location", {})
    lat, lon = float(loc.get("lat", 48.8566)), float(loc.get("lon", 2.3522))

    results = []
    cache = load_cache(data_dir)

    for rec in recs:
        band = infer_band(rec)
        if band is None:
            log(f"[resolve] {rec_label(rec)}: no band/frequency annotated, skipping")
            continue
        freq = float(rec["frequency_khz"])
        when = dt.datetime.fromisoformat(rec["capture_utc"])
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        when = when.astimezone(dt.timezone.utc)

        candidates = []
        if band == "FM":
            note = "FM: no international schedule database; identify from local band plan"
            candidates = []
        elif band == "AM":
            candidates = eibi_match(when, freq, args.sw_tolerance, data_dir)
            candidates += mwlist_match(freq, area=1)
        else:  # SW
            candidates = eibi_match(when, freq, args.sw_tolerance, data_dir)
            if args.sw_delay > 0:
                time.sleep(args.sw_delay)
            html = fetch_cached(freq, cache, args.sw_cache_ttl, data_dir, args.sw_delay)
            if html:
                candidates += parse_swi(html, when, lat, lon)
            else:
                log(f"[resolve] short-wave.info query failed for {rec['name']} (freq {freq})")

        seen = set()
        dedup = []
        for c in candidates:
            key = (c["db"], c["station"], c.get("start_utc", ""))
            if key in seen:
                continue
            seen.add(key)
            dedup.append(c)

        candidates = sorted(dedup, key=lambda c: c.get("distance_km") if c.get("distance_km") is not None else 1e9)
        results.append({
            "file": rec["file"],
            "name": rec["name"],
            "capture_utc": when.isoformat(timespec="seconds"),
            "band": band,
            "frequency_khz": freq,
            "candidates": candidates,
        })
        print(f"\n{rec_label(rec)}  {band} {freq:g} kHz  {when.isoformat(timespec='seconds')}" + (f"  (black/skip)" if seems_black(rec) else ""))
        if band == "FM":
            print("   FM: local-frequencies only, no DB lookup")
        for c in candidates[:12]:
            dist = f" {c['distance_km']}km" if c.get("distance_km") else ""
            print(f"   [{c['db']:14s}] {c['station'][:34]:34s} {c.get('start_utc','')}-{c.get('end_utc','')} {c.get('lang','')} {dist}")

    save_cache(data_dir, cache)

    out = data_dir / "resolved.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    log(f"[resolve] wrote {out} ({len(results)} captures resolved)")
    return 0


if __name__ == "__main__":
    sys.exit(main())