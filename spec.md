# sw-log skill — specification

Reproducible spec for the `sw-log` opencode skill: converting phone photos of a
radio frequency display (SW/AM/FM) into a station log (date/time, frequency,
band, station, listening location).

Development ran in `/home/dlyakh/src/swl-log/` (a git repo) with the skill at
`.agents/skills/sw-log/`. This document records the requirements, the verified
facts the design rests on, the exact interfaces, and the planned refinements.

---

## 1. Purpose

The user takes photos of a radio's LCD display (the tuned frequency) with a
phone and connects the phone to the PC over USB. The skill turns a batch of
such photos into a table of which broadcast stations were received and when:

- one row per photo: **capture date/time (UTC)**, **frequency**, **band**,
  **station** (or "unresolved"), **location**;
- append rows to a CSV log (`.sw-log/sw-log.csv`);
- remember what was already logged so subsequent runs only handle new photos.

## 2. Requirements (implemented)

1. Scan the connected phone's Photos directory for new image files.
2. For each new photo compute an approximate **UTC capture time** (the phone
   stores no EXIF capture time).
3. Skip essentially-black photos (screen off) automatically; flag dark ones for
   the reader to double-check.
4. Recognise **band** (SW/AM/FM) and **frequency** by reading the display —
   primarily via the agent's image vision (OCR was proven too weak).
5. Resolve (frequency, UTC time, band) to a **broadcast station** using online
   and offline schedule databases; list plausible candidates sorted by distance
   from the listening location.
6. Produce and persist a summary table; mark processed files so scans are
   incremental.
7. Skill must be reproducible/editable: implement the spec in `SKILL.md` +
   `references/` + `scripts/` under `.agents/skills/sw-log/` (current git repo
   scope); scripts may use HTTP(S).
8. Only stdlib Python is allowed (pip is blocked by PEP 668 on this machine);
   CLI tools `exiftool`, `identify`/`convert` (ImageMagick), and `tesseract` may
   be shelled out to.

## 3. Inputs, photos, environment (verified facts)

- **Mount**: `/media/dlyakh/FD2B-F90B/Photos/` — the basic phone when connected
  via USB.
- **Photos**: 640x480 JPEG. Two naming schemes:
  - `DSC_0000252.jpg` — sequential counter;
  - `1696151323591.jpg` — a 13-digit Unix epoch **milliseconds** timestamp
    (valid; usable as a UTC capture-time source).
- **EXIF**: tested files carry no `DateTimeOriginal`/`CreateDate`, so mtime or
  filename must be used.
- **Dark batch**: the 2026-09-09 session (DSC_0000235..0252) has mean brightness
  ≈ 0.02–0.09 (last 8 ≈ black); older/bright photos (e.g. DSC_0000234) ≈ 0.77.
- **OCR reality**: `tesseract` (5.3.4) produces garbage on these photos even
  after enhancement; it must be treated as a low-confidence hint at best
  (`AM 0A A ASS 7 5 4 SH 4 .` was the output for a bright photo).
- **Tool chain**: python3 3.14 (linuxbrew), no pip installs possible;
  `tesseract`, `exiftool`, ImageMagick `identify`/`convert` present.
- **Skill format**: agentskills.io — `SKILL.md` with `name` (must equal the
  directory name) and `description` in frontmatter; optional `scripts/` and
  `references/`; SKILL.md < 500 lines.

## 4. Timestamp model and the double-timezone bug

For the Sept 9 2026 batch the user recalled taking the photos ~20:00 local
(Europe/Paris). The filesystem mtime showed `2026-09-09 23:58 +02:00`
(≈ 21:58 UTC), i.e. mtime ≈ capture **+ 4 hours**. Explanation: the phone's
clock stores a wrong +2h offset, and the PC adds its own local-time shift again,
so the stored "local" time is double-shifted.

Model (config `mtime_to_utc_offset_hours`, default **-4.0**):

```
capture_utc_epoch = mtime_epoch + mtime_to_utc_offset_hours * 3600
```

Timestamp sources, priority order (recorded as `time_source`):

1. **EXIF** capture tag — interpreted as local wall clock in `local_tz`
   (Europe/Paris), respecting `OffsetTimeOriginal` if present (`time_source=exif`);
2. **filename epoch-ms** (`time_source=filename-epoch-ms`);
3. **mtime + offset** (`time_source=mtime+offset`).

Calibration: `scan_photos.py --calibrate FILE --real-local "YYYY-MM-DD HH:MM:SS"`
computes the implied offset from one known capture; update config and re-scan.

> Timestamp status (2026-09-10): the **-4.0 default is confirmed** for the basic
> phone. Phone UI shows 21:31 on `DSC_0000250.jpg` while mtime is 21:31:58 UTC —
> the phone clock is ~+2h fast, so the true capture is 19:31 Paris = 17:31 UTC,
> exactly what −4h yields (`--calibrate` ⇒ `-4.000`). The offset is stored per
> directory (`config.directories[<path>]`); other devices may differ and should
> be calibrated. One earlier lead — the watch reading on `DSC_0000235` (15:25)
> implied −2h — was a misread ground truth (that wristwatch runs ~+2h fast);
> dismissed in favour of the phone-UI check.

## 5. Config and state layout (`.sw-log/`, fully git-ignored)

- `config.json` (auto-created by scan) — defaults:

  ```json
  {
    "photos_dir": "/media/dlyakh/FD2B-F90B/Photos",
    "local_tz": "Europe/Paris",
    "location": {"name": "Paris, France", "lat": 48.8566, "lon": 2.3522},
    "mtime_to_utc_offset_hours": -4.0,
    "black_threshold": 0.05,
    "directories": {"<abs photo dir>": {"mtime_to_utc_offset_hours": -4.0}}
  }
  ```

  `photos_dir` is only the default seed for auto-detection; `directories` holds
  per-device overrides (offset, timezone, threshold).

- `state.json` — **v2**, per directory:

  ```json
  {
    "version": 2,
    "directories": {
      "/media/dlyakh/FD2B-F90B/Photos": {"processed": ["<full path>"], "last_mtime": 1757523118.0}
    }
  }
  ```

  (v1 `{"processed": [...], "last_mtime": ...}` is migrated automatically.)
- `pending.json` — `{"records": [...]}`; each record:

  ```json
  {
    "file": "/abs/path/name.jpg",
    "name": "name.jpg",
    "directory": "/abs/photo/dir",
    "mtime_epoch": 1757523118.0,
    "mtime_local": "2026-09-09T23:31:58+02:00",
    "capture_utc": "2026-09-09T17:31:58+00:00",
    "time_source": "mtime+offset",
    "mean_brightness": 0.029,
    "std_brightness": 0.03,
    "black": true,
    "status": "new",
    "frequency_khz": null,
    "band": null,
    "recognized": false,
    "notes": []
  }
  ```

  `band`/`frequency_khz`/`recognized`/`notes` are filled by the agent after
  reading the photos. **`frequency_khz` is always kHz** (e.g. 94.3 MHz → 94300).
- `resolved.json` — resolver output (see §7).
- `sw-log.csv` — the final append-only log, maintained by the agent, header:
  `date_utc,time_utc,freq_khz,band,station,location,notes`.
- `eibi/` — cached EiBi season text + parsed `schedule.jsonl` + `meta.json`.
- `work/` — enhanced copies produced by `recognize.py` (source photos never
  modified).

## 6. Pipeline and script interfaces

Stage flow: **scan → read (vision) → resolve → pick → emit → commit**.

### `scripts/scan_photos.py` (stage 1)
- `python3 scan_photos.py [-P DIR] [--data-dir .sw-log] [--black-threshold F] [--since ISO] [--list-dirs]`
- No `-P`: auto-scans every remembered directory (state) plus the default seed
  (`config.photos_dir`) that is currently present; absent ones are reported as
  skipped, exit 2 only if nothing is mounted. With `-P`: scans only that
  directory (exit 2 if missing).
- `--commit` — mark the current pending list processed; routes each record's
  full file path into its directory's `processed` set (updates `state.json`).
- `--calibrate FILE --real-local "YYYY-MM-DD HH:MM:SS" [--apply]` — print (and
  with `--apply` persist per-directory) the implied mtime→UTC offset.
- `--list-dirs` — print remembered directories (present/absent, last-scan,
  processed count) and exit.
- Output: one candidate line (`dir/name`) per new photo + `pending.json`
  rewritten; remembered directories' `last_mtime` refreshed.

### `scripts/recognize.py` (stage 2, vision helper)
- `python3 recognize.py FILE... [--data-dir .sw-log] [--work-dir DIR] [--scale 400%]`
- Writes `<work>/<stem>_norm.png`, `_stretch.png`, `_lat.png` (gray + resize +
  normalize / auto-level-gamma / threshold) via ImageMagick `convert`; runs
  `tesseract --psm 6` with whitelist `0123456789.kMHzAMFWSW`; prints JSON
  `{file, variants[], ocr_hint, ocr_confidence:"low"}`.

### `scripts/eibi.py` (EiBi downloader/parser/cache)
- `python3 eibi.py --data-dir .sw-log --date ISO [--freq kHz] [--refresh] [--list-urls]`
- Selects season file whose header `Valid <start> - <end>` covers the date;
  caches the txt in `eibi/`; writes `schedule.jsonl` (one JSON row per line).
- Fixed-width row layout (verified against `freq-a26.txt`):

  `freq[0:14] time[14:23] days[23:30] itus[30:34] station[34:59] lang[59:63] target[63:74] remarks[74:]`
- Time: `HHMM-HHMM` UTC (`2400` = end of day). Days: blank = daily,
  `1234567` (1=Mon..7=Sun), `Mo-Sa`, `Th-Tu`, `1.Sa`/`2.Su` (approx. weekly).

### `scripts/resolve_station.py` (stage 3)
- `python3 resolve_station.py [--input .sw-log/pending.json] [--data-dir .sw-log] [--sw-tolerance 0.5]`
- Band inference: `>= 60000` → FM; `150..1710` → AM; `>= 2300` → SW; else the
  agent must annotate explicitly.
- SW: EiBi + short-wave.info; AM: EiBi + MWLIST(area=1); FM: no database.
- Filters by UTC time and weekday; dedups; sorts by distance from
  `config.location`. Writes `.sw-log/resolved.json`. Requires a Firefox-style
  User-Agent on all HTTP calls.

## 7. Broadcast databases (verified 2026-09-10)

- **short-wave.info** — `http://www.short-wave.info/index.php?freq=<kHz>`
  (must be `http`), table `<table id="output">`; cells: freq (may carry a
  `&#8201;` thin space → `html.unescape`), station, start `HH:MM` UTC, end,
  days `1234567`, lang, power, azimuth, site (text contains
  `Latitude: 30°04'N Longitude: 31°14'E`). Times/days filtered client-side.
  Example verified: freq=9410, Wed 2026-09-09 19:05 UTC → Radio Cairo
  19:00–20:00 German, ~3046.6 km from Paris.
- **EiBi** — `http://www.eibispace.de/dx/freq-<season>.txt`; season
  `a<yy>` ≈ Mar–Oct, `b<yy>` ≈ Oct–Mar. Presence as of the dev date:
  `freq-a26.txt`, `freq-b25.txt`, `freq-a25.txt` OK; `freq-b26.txt`,
  `freq-a24.txt` → 404 (not yet published; select by validity window).
  Header example: `Valid March 29, 2026 - October 25, 2026`.
- **MWLIST** — `https://www.mwlist.org/mwlist_quick_and_easy.php?area=1&kHz=<kHz>`,
  area=1 = Europe/Africa/Middle East. Page is **latin-1** (decode as latin-1,
  else non-ASCII names like České Budějovice mojibake). Row cells: country,
  (blank), `StationName HHMM-HHMM lang` or `Name 24h lang`, transmitter (+`*` =
  high precision), (blank), power kW, (blank), remarks. Hours cell has **unknown
  timezone reference** → do not hard-filter by time.
- **shortwave.live** — returns HTTP 500 for frequency queries (2026-09-10);
  documented as unusable/manual-only. Not used by scripts.
- **FM** — no schedule DB; local band plan only.

## 8. Skill surface

- `SKILL.md` (100 lines) — frontmatter `name: sw-log`, `description`; workflow:
  1) mount check `[ -d /media/dlyakh/FD2B-F90B/Photos ]`, 2) scan, 3) read
  photos w/ `recognize.py` + vision → annotate `pending.json`, 4) resolve, 5)
  pick station + notes, 6) summary table (UTC) + CSV append, 7) `--commit`.
- `references/workflow.md` — runbook, state layout, idempotency/interrupt rules.
- `references/reading-displays.md` — reading LCD values; kHz vs MHz rules and
  inference table; error list.
- `references/timestamps.md` — the −4h bug, calibration procedure.
- `references/databases.md` — verified endpoint/format details, UA requirement,
  plausibility cross-checks (SW 1000+ km, AM 200–1500 km at night, FM < 150 km).

## 9. Multi-directory / multi-device support (implementation of the planned refinement)

Originally planned (§9 of the first spec revision); **implemented and tested**
(2026-09-10). Rationale: photos can come from different devices (basic phone vs
smartphone, different USB mount paths), each with its own last-scan mark and,
potentially, its own timestamp-offset calibration.

### Behaviour implemented

1. **Explicit directory parameter** — `-P`/`--photos-dir DIR` scans only that
   directory; it is remembered even before a commit.
2. **Remember scanned directories** — `state.json` is now v2:
   `{"version": 2, "directories": {<abs path>: {"processed": [<full paths>],
   "last_mtime": <epoch>}}}`. Scanning updates `last_mtime`; committing appends
   to `processed` (keyed by full file path, making re-scans incremental). A
   one-time migration converter (`migrate_state_v1`) lifts the legacy
   single-directory `processed`/`last_mtime` into the new shape (lazily, on any
   mutating run).
3. **Auto-detect present devices** — with no `--photos-dir`, the scan iterates
   the remembered directories plus the default seed dir (`config.photos_dir`),
   scans those that are currently present, and reports absent ones as
   `skipped (not mounted)` without failing. Exit 2 only if *nothing* is mounted.

### Per-device configuration

- `config.directories[<path>]` holds device-specific overrides for the three
  device-dependent keys: `mtime_to_utc_offset_hours`, `local_tz`,
  `black_threshold` (global config supplies defaults, `dir_config()` merges).
- The listening **location stays global** (same room, regardless of device).
- `--calibrate FILE --real-local "…" [--apply]` computes and (with `--apply`)
  persists the per-directory offset — use it once per device.

### CLI (final)

```
scan_photos.py [-P DIR] [--data-dir .sw-log] [--black-threshold F] [--since ISO]
               [--commit] [--list-dirs] [--calibrate FILE --real-local "…" --apply]
```

- `--commit` routes each pending record to its `directory`'s state entry.
- `--list-dirs` prints remembered dirs with present/absent, last-scan, processed.
- Pending records now carry `"directory": "<abs path>"`; resolver labels and
  `resolved.json` per-device using it.

### Impact on other components

- `resolve_station.py` prints `<dirbasename>/<name>` labels to disambiguate
  equal `DSC_*.jpg` names across devices.
- `SKILL.md` and `references/workflow.md` updated: device selection step
  (`--list-dirs`, auto vs `-P`), per-device commit, per-device calibration.
- `references/timestamps.md` updated: offset is per directory, apply mode.
- Exit-code contract: 0 ok; 2 no directory (requested missing or none mounted).