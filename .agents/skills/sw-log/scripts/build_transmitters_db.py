#!/usr/bin/env python3
"""Build a transmitter-site database from short-wave.info (and later mwlist).

For every (frequency, station) we scrape from short-wave.info we extract the
transmitter *site* (name, country, latitude/longitude, radiated power, and the
beam azimuth of a directional antenna). Sites are deduplicated by coordinates so
that the same tower reported on several frequencies collapses into one record.

The result is written to <data-dir>/transmitters.json and is meant to be reused
by the resolver (to rank candidates by power-adjusted signal strength as seen
from the listener's location) and by future research.

Usage:
  python3 build_transmitters_db.py [--data-dir .sw-log] [--freqs 3955,3995,...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import html
import time
import urllib.request
import urllib.error
from pathlib import Path

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0"
SCRIPT_DIR = Path(__file__).resolve().parent


def fetch(url, encoding="utf-8", timeout=30, tries=3, delay=10.0):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            r = urllib.request.urlopen(req, timeout=timeout)
            return r.read().decode(encoding, "replace")
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code == 429 and i < tries - 1:
                time.sleep(delay * (i + 1))
                continue
            return None
        except Exception as e:  # noqa: BLE001
            last = str(e)
            if i < tries - 1:
                time.sleep(delay)
                continue
            return None
    print(f"  [warn] fetch failed {url}: {last}")
    return None


def coord_field(text, prefix):
    """Extract signed decimal coord from 'Latitude: 51.3°21.1N' style strings.

    The degree sign is injected as chr(0xb0) so the source never carries a literal
    multi-byte character that could be mangled by editors/terminals.
    """
    if not text:
        return None
    DEG = chr(0x00b0)  # °
    # Accept '39°21\'N', '39°21.1\'N', '51.30°21.10\'N' (deg/minutes int or dec, optional ').
    # A colon after the label ("Latitude:") is optional.
    pat = prefix + r":?\s*([0-9]+(?:\.[0-9]+)?)\s*" + DEG + r"\s*([0-9]+(?:\.[0-9]+)?)\s*'?\s*([NSEW])"
    m = re.search(pat, text)
    if not m:
        return None
    deg, mn, hem = float(m.group(1)), float(m.group(2)), m.group(3)
    val = deg + mn / 60.0
    if hem in ("S", "W"):
        val = -val
    return val


def parse_site(raw):
    """raw is the 'site' cell, e.g. 'Droitwich, England, UK\nLatitude: ... Longitude: ...'"""
    if not raw:
        return None
    lat = coord_field(raw, "Latitude")
    lon = coord_field(raw, "Longitude")
    if lat is None or lon is None:
        return None
    # country line is the text before the first "Latitude:"
    country = re.split(r"\s*Latitude:", raw, maxsplit=1)[0].strip()
    country = re.sub(r"\s+", " ", country)
    return {"lat": lat, "lon": lon, "country": country}


def parse_table(doc):
    """Return list of station records from a short-wave.info output table."""
    if not doc:
        return []
    m = re.search(r'<table[^>]*id="output"[^>]*>(.*?)</table>', doc, re.S)
    if not m:
        return []
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip().replace(" ", "")
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 8 or not cells[0].isdigit():
            continue
        freq = float(cells[0])
        station = cells[1]
        start, end, days, lang = cells[2], cells[3], cells[4], cells[5]
        power = cells[6]
        azimuth = cells[7]
        site = cells[8] if len(cells) > 8 else ""
        siteinfo = parse_site(site)
        out.append({
            "frequency_khz": freq, "station": station, "start_utc": start, "end_utc": end,
            "days": days, "lang": lang, "power_kw": power, "azimuth": azimuth,
            "site": re.sub(r"\s+", " ", site).strip(), "site_info": siteinfo,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=".sw-log")
    ap.add_argument("--freqs", default="", help="comma-separated kHz list (default: all in pending.json)")
    ap.add_argument("--delay", type=float, default=10.0, help="seconds between queries")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).resolve()
    cfg = json.loads((data_dir / "config.json").read_text()) if (data_dir / "config.json").exists() else {}
    loc = cfg.get("location", {})
    lat0, lon0 = float(loc.get("lat", 48.8566)), float(loc.get("lon", 2.3522))

    # Determine freqs
    freqs = []
    if args.freqs:
        freqs = [int(x) for x in args.freqs.split(",") if x.strip()]
    else:
        pend = json.loads((data_dir / "pending.json").read_text())
        freqs = sorted({int(r["frequency_khz"]) for r in pend["records"]
                        if r.get("band") == "SW" and r.get("frequency_khz")})

    sites = {}
    for f in freqs:
        doc = fetch(f"http://www.short-wave.info/index.php?freq={f}", delay=args.delay)
        for rec in parse_table(doc):
            si = rec["site_info"]
            if not si:
                continue
            key = (round(si["lat"], 4), round(si["lon"], 4))
            s = sites.get(key)
            if not s:
                az = rec["azimuth"]
                m = re.match(r"([0-9]+)", az)
                s = {
                    "lat": si["lat"], "lon": si["lon"], "country": si["country"],
                    "power_kw": rec["power_kw"], "azimuth_deg": int(m.group(1)) if m else None,
                    "frequencies": [], "stations": [], "source": "short-wave.info",
                }
                sites[key] = s
            if rec["power_kw"] and not s["power_kw"]:
                s["power_kw"] = rec["power_kw"]
            if s["azimuth_deg"] is None and rec["azimuth"]:
                m = re.match(r"([0-9]+)", rec["azimuth"])
                s["azimuth_deg"] = int(m.group(1)) if m else None
            if rec["frequency_khz"] not in s["frequencies"]:
                s["frequencies"].append(rec["frequency_khz"])
            if rec["station"] not in s["stations"]:
                s["stations"].append(rec["station"])
        if args.delay > 0:
            time.sleep(args.delay)

    # Derive distance + azimuth from the observer location
    def to_rad(x):
        return math.radians(x)

    out_sites = {}
    for (la, lo), s in sites.items():
        d = 2 * 6371.0 * math.asin(math.sqrt(
            math.sin((to_rad(la) - to_rad(lat0)) / 2) ** 2 +
            math.cos(to_rad(lat0)) * math.cos(to_rad(la)) * math.sin((to_rad(lo) - to_rad(lon0)) / 2) ** 2))
        # initial bearing
        brng = math.degrees(math.atan2(
            math.sin(to_rad(lo) - to_rad(lon0)) * math.cos(to_rad(la)),
            math.cos(to_rad(lat0)) * math.sin(to_rad(la)) -
            math.sin(to_rad(lat0)) * math.cos(to_rad(la)) * math.cos(to_rad(lo) - to_rad(lon0))))
        s["distance_km"] = round(d, 1)
        s["azimuth_from_paris"] = round((brng + 360) % 360, 1)
        out_sites[f"{s['country']} ({la},{lo})"] = s

    result = {
        "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "observer": {"lat": lat0, "lon": lon0,
                     "place": cfg.get("location_name", "Paris, France")},
        "site_count": len(out_sites),
        "sites": dict(sorted(out_sites.items(), key=lambda kv: kv[1]["distance_km"])),
    }
    out = data_dir / "transmitters.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"[build_transmitters] {len(freqs)} freqs -> {len(out_sites)} unique sites -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
