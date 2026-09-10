# Broadcast schedule databases

Reference for `resolve_station.py`. All of the scrape/parse details below were
verified live against real responses (2026-09-10). If a URL or format ever
changes, re-inspect the raw output before adapting a parser.

**Golden rule: HTTP requests from scripts always send this User-Agent**, or
servers refuse the connection:

```
Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0
```

## short-wave.info (SW)

- URL: `http://www.short-wave.info/index.php?freq=<kHz>`  (note: `http`, and the
  host redirects trailing-slash paths; plain `index.php?freq=...` works)
- Returns the 30-day "what's on now" window around today for that frequency.
  Rows are NOT filtered by day or time by the server — that happens client-side.
- Table selector: `<table id="output">`. Row cells (verified):

  | # | content |
  |---|---------|
  | 0 | frequency, e.g. `9410` (may contain a thin-space entity `&#8201;` → strip/unescape, then `isdigit()` passes) |
  | 1 | station name |
  | 2 | start time `HH:MM` UTC |
  | 3 | end time `HH:MM` UTC |
  | 4 | days, e.g. `1234567` (7 chars, `1`=Monday) |
  | 5 | language |
  | 6 | power kW |
  | 7 | azimuth |
  | 8 | transmitter site text, contains `Latitude: 30°04'N Longitude: 31°14'E` |

- Times/days filter: compare against the UTC capture datetime; weekday =
  `capture.weekday() + 1` (so Monday=1 matches char index 0 of the days string).
- Encodings gotchas seen: `&#8201;` thin space inside the frequency cell,
  `&#8201;`/`&#8202;` inside station names — `html.unescape()` each cell first.
- Example: `freq=9410` on Wed 2026-09-09 19:05 UTC → Radio Cairo 19:00–20:00
  German, site ~3046 km from Paris.
- The plain-site query read-only (`index.php?freq=`) is the one that works.
  `/misc/languages.php` access pages and some others can be flaky/rate-limited.
  Rate-limit: keep queries focused (one per unresolved SW photo).

## EiBi (SW + international MW)

- Dataset: `http://www.eibispace.de/dx/freq-<season>.txt`, one flat file per
  broadcast season. Season codes: `a<yy>` ≈ Mar–Oct (summer), `b<yy>` ≈
  Oct–Mar (winter), e.g. `freq-a26.txt` = Mar 29 2026 – Oct 25 2026.
  Future filenames may 404 until published — pick by *validity window*, not
  by name.
- Each file's header has `Valid <start> - <end>` (month-first, e.g.
  `March 29, 2026 - October 25, 2026`). Parse that line to select the season
  covering the capture date and ignore 404s for seasons not yet published.
- Fixed-width rows (verified against `freq-a26.txt`):

  | slice | field | notes |
  |-------|-------|-------|
  | `[0:14]`  | freq kHz | may be decimal (e.g. `6155.0`) |
  | `[14:23]` | `HHMM-HHMM` | UTC; `2400` = end of day |
  | `[23:30]` | days | blank = daily; `1234567` = Mon..Sun; `Mo-Sa`; `1.Sa`; `2356` |
  | `[30:34]` | ITU country |  |
  | `[34:59]` | station |  |
  | `[59:63]` | language |  |
  | `[63:74]` | target area |  |
  | `[74:]`   | remarks |  |

- `days` semantics (EiBi): `1`=Monday … `7`=Sunday. Files use BOTH numeric
  dot-form `1......` / `1234...` AND abbreviation ranges `Mo-Sa`, `Th-Tu`.
  `1.Sa`, `2.Su` = "1st Saturday of month" (approximate as weekly).
- This is the only source for international **AM/MW** (e.g. 954 kHz) stations.

## MWLIST (AM / MW Europe)

- URL: `https://www.mwlist.org/mwlist_quick_and_easy.php?area=1&kHz=<kHz>`
  (`area=1` = Europe / Africa / Middle East; the other areas cover the rest of
  the world).
- Page is **latin-1** encoded; decode as `latin-1` (utf-8 fails or mojibakes
  non-ASCII station names like České Budějovice).
- Table row cells (verified for `kHz=954`):

  | # | content |
  |---|---------|
  | 0 | country code (e.g. `CZE`, `E`, `ETH`) |
  | 1 | (blank image cell) |
  | 2 | `StationName HHMM-HHMM lang` or `Name 24h lang` — name, hours, language |
  | 3 | transmitter site (+ `*` = high precision) |
  | 4 | (blank) |
  | 5 | power kW |
  | 7 | remarks (e.g. callsign history) |

- Hours in the station cell have *unknown timezone of reference* (usually local
  to transmitter or UTC — contributors vary). Do not hard-filter by time; list
  all stations on the frequency with their hours string and let the agent
  reason about propagation/time plausibility.
- MWLIST typically lists *regular/registered* stations; pirates and unregistered
  low-power ops will not appear.

## shortwave.live ("What's On" / SW schedule) — UNUSABLE as of 2026-09-10

- `https://shortwave.live/search?q=<freq>` and the API POST endpoint both return
  HTTP 500 for frequency queries. Do NOT rely on it. If a future session
  discovers it works again, prefer it over short-wave.info (it is more accurate
  and timezone-correct), but do not block the pipeline on it.

## FM

- No schedule database: FM is inherently local (100 km range). Candidates are
  left empty; the agent identifies the station from the local band plan /
  site-name knowledge or by listening. Do not query any of the above for FM.

## Cross-check / verification

1. EiBi and short-wave.info should agree on the same station for an SW capture;
   when they disagree, trust short-wave.info's per-day/time filtering and flag
   the discrepancy in the notes.
2. Sanity-check plausibility from the capture location (default Paris):
   - SW: 1000–1500+ km hops are normal (e.g. Cairo ≈ 3050 km).
   - AM: 200–1500 km at night.
   - FM: < 150 km.
3. If a candidate has no distance (no transmitter lat/lon), keep it but mark it
   as unverifiable-by-distance.