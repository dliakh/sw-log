#!/usr/bin/env python3
"""Download and parse the EiBi shortwave/mw schedule dataset, selected by date.

EiBi (http://eibispace.de) publishes the most comprehensive free broadcast
schedule, one flat text file per season:

    http://www.eibispace.de/dx/freq-a26.txt   (A26 valid Mar..Oct 2026)
    http://www.eibispace.de/dx/freq-b25.txt   (B25 valid Oct 2025..Mar 2026)

Each file's header contains a "Valid <start> - <end>" line. This script picks
the season whose validity window contains the requested date, caches it under
<data-dir>/eibi/, parses the fixed-width rows, and writes a JSON-lines file.

Column layout used (verified against freq-a26.txt):
    freq    [0:14]    time    [14:23]    days    [23:30]
    itus    [30:34]   station [34:59]    lang    [59:63]
    target  [63:74]   remarks [74:]
Rows: freq(kHz, may be decimal) | HHMM-HHMM UTC (2400 = midnight) | days
(blank = daily, "1234567" = Mon..Sun, "Mo-Sa", ...) | ITU country | station |
language | target area | remarks.

Usage:
  python3 eibi.py --data-dir .sw-log --date "2026-09-09T21:00:00Z" [--freq 9410]
  python3 eibi.py --data-dir .sw-log --list-urls
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

BASE = "http://www.eibispace.de/dx/"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0"

DAY_ABBR = {"Mo": 0, "Tu": 1, "We": 2, "Th": 3, "Fr": 4, "Sa": 5, "Su": 6}
DAILY = [0, 1, 2, 3, 4, 5, 6]


def fetch(url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("latin-1", errors="replace")
    except Exception as exc:  # noqa: BLE001
        print(f"[eibi] failed to fetch {url}: {exc}", file=sys.stderr)
        return None


def valid_range(text: str) -> tuple[dt.date, dt.date] | None:
    DATES = ("%B %d, %Y", "%d %B %Y", "%d %b %Y", "%B %Y")
    m = re.search(r"Valid\s+(.+?)\s*[-–]\s*(.+?)\s*(?:\r?\n|$)", text)
    if not m:
        return None
    for fmt in DATES:
        try:
            return (
                dt.datetime.strptime(m.group(1), fmt).date(),
                dt.datetime.strptime(m.group(2), fmt).date(),
            )
        except ValueError:
            continue
    return None


def candidate_urls(year: int, extra_years: int = 1) -> list[str]:
    out = []
    for y in range(year - extra_years, year + 1):
        yy = f"{y % 100:02d}"
        out.append(f"{BASE}freq-a{yy}.txt")
        out.append(f"{BASE}freq-b{yy}.txt")
    return out


def parse_days(raw: str):
    """Return (weekday list Mon..Sun as [0..6], approx:bool)."""
    raw = raw.strip()
    if not raw:
        return DAILY, False

    if re.fullmatch(r"[1-7.]+", raw):
        days = [i for i, ch in enumerate(raw) if ch != "."]
        return days or DAILY, False

    names = re.findall(r"(?:Mo|Tu|We|Th|Fr|Sa|Su)", raw)
    if names:
        # expand simple ranges like Mo-Fr / Th-Tu (wrap allowed), or singles
        out = set()
        for a, b in zip(names[::2], names[1::2]):
            i, j = DAY_ABBR[a], DAY_ABBR[b]
            if i <= j:
                out.update(range(i, j + 1))
            else:
                out.update(range(i, 7))
                out.update(range(0, j + 1))
        if len(names) % 2 == 1:
            out.add(DAY_ABBR[names[-1]])
        return sorted(out) or DAILY, True

    # "1.Sa", "2.Su", "2356" ... -> approximation, treat as weekly on those days
    for abbr, idx in DAY_ABBR.items():
        if abbr in raw:
            return sorted({idx}), True
    return DAILY, True


def parse_rows(text: str, target_freq: float | None):
    rows = []
    for line in text.splitlines():
        if not line or not line[0].isdigit():
            continue
        if len(line) < 34:
            continue
        freq_s = line[0:14].strip()
        time_s = line[14:23].strip()
        if not time_s or "-" not in time_s:
            continue
        try:
            freq = float(freq_s)
        except ValueError:
            continue
        if target_freq is not None and abs(freq - target_freq) > 0.001:
            continue
        days_s = line[23:30].strip()
        itus = line[30:34].strip()
        station = line[34:59].strip()
        lang_s = line[59:63].strip()
        target = line[63:74].strip()
        remarks = line[74:].strip()

        mm = re.fullmatch(r"(\d{2})(\d{2})-(\d{2})(\d{2})", time_s.replace(" ", ""))
        if not mm:
            continue
        start_min = int(mm.group(1)) * 60 + int(mm.group(2))
        end_min = int(mm.group(3)) * 60 + int(mm.group(4))
        if end_min == 1440:
            end_min = 1440  # represents end-of-day; handled by matcher

        days, approx = parse_days(days_s)
        rows.append({
            "freq": freq,
            "start_min": start_min,
            "end_min": end_min,
            "days": days,
            "days_approx": approx,
            "itus": itus,
            "station": station,
            "lang": lang_s,
            "target": target,
            "remarks": remarks,
            "raw": line,
        })
    return rows


def select_dataset(data_dir: Path, when: dt.datetime, force: bool) -> Path | None:
    eibi_dir = data_dir / "eibi"
    eibi_dir.mkdir(parents=True, exist_ok=True)

    fetched = []
    for url in candidate_urls(when.year):
        cache = eibi_dir / os.path.basename(url)
        if not cache.exists() or force:
            text = fetch(url)
            if text is None:
                continue
            cache.write_text(text, encoding="latin-1")
        else:
            text = cache.read_text(encoding="latin-1")
        rg = valid_range(text)
        if rg and rg[0] <= when.date() <= rg[1]:
            print(f"[eibi] using {url} (valid {rg[0]} .. {rg[1]})", file=sys.stderr)
            text = cache.read_text(encoding="latin-1")
            jsonl = eibi_dir / "schedule.jsonl"
            meta = eibi_dir / "meta.json"
            rows = parse_rows(text, None)
            with jsonl.open("w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            meta.write_text(json.dumps({
                "source": url, "valid_start": str(rg[0]), "valid_end": str(rg[1]),
                "rows": len(rows), "date_used": when.isoformat(),
            }, indent=2))
            fetched.append(url)
            return jsonl
        fetched.append(url)
    print(f"[eibi] no season file covering {when.date()} (tried: {', '.join(fetched)})", file=sys.stderr)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=".sw-log", help="project-local data dir")
    ap.add_argument("--date", help="ISO datetime the schedule must cover; default: now")
    ap.add_argument("--freq", type=float, help="only retain rows near this kHz frequency")
    ap.add_argument("--refresh", action="store_true", help="re-download despite cache")
    ap.add_argument("--list-urls", action="store_true", help="print candidate URLs and exit")
    args = ap.parse_args()

    if args.list_urls:
        print("\n".join(candidate_urls(dt.date.today().year)))
        return 0

    if args.date:
        when = dt.datetime.fromisoformat(args.date)
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        when = when.astimezone(dt.timezone.utc)
    else:
        when = dt.datetime.now(dt.timezone.utc)

    data_dir = Path(args.data_dir).resolve()
    jsonl = select_dataset(data_dir, when, args.refresh)
    if jsonl is None:
        return 1

    rows = []
    for ln in jsonl.read_text(encoding="utf-8").splitlines():
        row = json.loads(ln)
        if args.freq is not None and abs(row["freq"] - args.freq) > 0.001:
            continue
        rows.append(row)

    print(f"[eibi] dataset: {jsonl}  ({len(rows)} rows for freq={args.freq})")
    for r in rows[:10]:
        print(f"  {r['freq']:>9g} {r['start_min']//60:02d}:{r['start_min']%60:02d}-"
              f"{r['end_min']//60:02d}:{r['end_min']%60:02d} {''.join(map(str, r['days'])) or 'daily':7s} "
              f"{(r['itus'] or ''):4s} {r['station'][:24]:24s} {r['lang']:6s} {r['remarks']}")
    if len(rows) > 10:
        print(f"  ... {len(rows) - 10} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())