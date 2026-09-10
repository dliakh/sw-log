---
name: sw-log
description: Turns photos of a shortwave/AM/FM radio display (band + frequency) into a station log. Use when photos of a radio frequency display (local LCD screen showing the tuned frequency) were taken and need to be converted into a dated table of which broadcast station was being received. Produces the capture date/time, frequency, band, station name, and listening location.
---

# Shortwave / radio log from display photos

Convert photos of a radio's frequency display into a station log: for each
photo, determine the tuned band (SW/AM/FM) and frequency, compute the
approximate UTC capture time, resolve the broadcast station, and end up with a
date/time/frequency/station/location table.

## When to use

- New photos of a radio LCD display were added to the phone mount.
- The user wants the previous listen-sessions logged (station, time, frequency).
- Only if the listening location is known (defaults to Paris, France — see
  config).

Source photos: `/media/dlyakh/FD2B-F90B/Photos/` (voice/phone drive,
640x480 JPEGs, no EXIF). Everything is handled from the repo root
`/home/dlyakh/src/swl-log/`. Runtime state lives in `.sw-log/` (git-ignored).

## Run the pipeline

Follow `references/workflow.md` for full detail; the condensed steps:

1. **Select device(s)** — `python3 .agents/skills/sw-log/scripts/scan_photos.py --data-dir .sw-log --list-dirs`
   shows which photo directories are remembered and which are currently
   mounted. If the user names a device/path (or a directory is not remembered
   yet), scan it explicitly with `-P /path`. Otherwise auto-detect.
2. **Scan** — `python3 .agents/skills/sw-log/scripts/scan_photos.py --data-dir .sw-log`
   auto-scans every remembered directory that is currently present (plus the
   default seed dir); absent ones are only reported. Or `-P /path/to/Photos
   [--since "YYYY-MM-DDTHH:MM:SS"]` for one directory. Prints
   `dir/name ... capture=... black=...`, writes `.sw-log/pending.json`.
   Exit 2 = requested directory missing, or nothing mounted.
3. **Read each photo with your own vision** —
   `python3 .agents/skills/sw-log/scripts/recognize.py <photo> --data-dir .sw-log`
   prepares enhanced copies in `.sw-log/work/` (never modifies sources) and
   prints a low-confidence OCR hint. View the image, read band + frequency, and
   write them into the matching `pending.json` record
   (`band`, `frequency_khz` — always kHz, see
   `references/reading-displays.md`; `recognized: true`).
   - Frequency/band inference, ambiguity handling, and common errors are in
     `references/reading-displays.md`; read it if unsure.
   - Typical photograph session produces ~10–20 files; many of the last ones are
     just black (screen off) — `black:true` means skip, unless you can still see
     a display.
4. **Resolve stations** —
   `python3 .agents/skills/sw-log/scripts/resolve_station.py --data-dir .sw-log`
   queries EiBi (+ short-wave.info for SW, + MWLIST for AM) and writes
   `.sw-log/resolved.json`. FM has no database.
5. **Pick the station** — choose the most plausible candidate per capture (the
   resolver pre-sorts by distance from the configured location; SW 1000+ km is
   normal, FM < 150 km). Keep the reasoning in `notes`. If nothing fits or it
   is FM-local, log `station: null` with a note. Details:
   `references/databases.md`.
6. **Emit the summary table** — markdown table, all times UTC:

   | date (UTC) | time (UTC) | freq | band | station | location | notes |

   Showing `9410 kHz SW`, `94.3 MHz FM`, etc. `location` = listening site
   (default `Paris, France`). Append the same rows to `.sw-log/sw-log.csv`
   (header row written once).
7. **Commit** — `python3 .agents/skills/sw-log/scripts/scan_photos.py --data-dir .sw-log --commit`
   marks the pending files processed (per directory) so the next scan only picks
   up new photos on any device.

## Files

- `scripts/scan_photos.py` — stage 1: per-directory new-photo detection with
  multi-device support (`-P` explicit or auto-detect mounted remembered dirs),
  black triage, capture-time computation, `--list-dirs`, `--calibrate FILE
  --real-local ... [--apply]`, `--commit`.
- `scripts/recognize.py` — stage 2: enhanced variants + OCR hint for the
  vision reader.
- `scripts/resolve_station.py` — stage 3: resolve stations via the databases.
- `scripts/eibi.py` — EiBi season download/parse/cache (invoked by the
  resolver).
- `references/workflow.md` — full runbook, state layout, operational notes.
- `references/reading-displays.md` — reading the LCD display; kHz vs MHz rules.
- `references/timestamps.md` — the phone's -4h double-timezone bug and how to
  calibrate.
- `references/databases.md` — verified endpoint formats for short-wave.info,
  EiBi, MWLIST; plausibility cross-checks.

## Time / timestamp rules

- Match schedules in UTC. `capture_utc` is already UTC (see
  `references/timestamps.md` for the per-device mtime-offset story; run
  `--calibrate ... --apply` once per device when the user can give a real
  capture time).
- Weekday filters use the UTC weekday. Day-of-week numbers in EiBi rows are
  1=Mon..7=Sun, matching the resolver's convention.

## Environment / dependencies

- `python3` (stdlib only), `exiftool`, ImageMagick `identify`/`convert`,
  `tesseract`. No pip packages — everything is stdlib, so `pip` (blocked by
  PEP 668) is not needed.
- The resolver's Internet calls send a Firefox User-Agent; some endpoints
  (short-wave.info) refuse a default UA.

## Never

- Never modify/modify-beside the original photos (all work is on copies).
- Never commit `.sw-log/` (state, caches, work images).
- Never fabricate a station: an unresolved capture stays `station: null`.