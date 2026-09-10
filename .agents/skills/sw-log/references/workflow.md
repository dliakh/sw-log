# sw-log pipeline workflow

End-to-end run for converting a batch of radio-display photos into a station
log table. Scripts live in `scripts/`; all runtime state in `.sw-log/`.

## Files and state

| Path | Purpose |
|------|---------|
| `.sw-log/config.json` | global defaults (default seed dir, timezone, location, black threshold) + per-directory overrides in `directories` (auto-created) |
| `.sw-log/state.json` | v2: `{"version": 2, "directories": {<path>: {"processed": [...], "last_mtime": ...}}}` — one entry per device/directory ever scanned |
| `.sw-log/pending.json` | records from latest scan (each with its `directory`), **annotated** by the agent, then resolved |
| `.sw-log/resolved.json` | `resolve_station.py` output with candidate stations |
| `.sw-log/sw-log.csv` | append-only final log the agent maintains |
| `.sw-log/eibi/` | EiBi season file + `schedule.jsonl` cache |
| `.sw-log/work/` | `recognize.py` enhanced copies (never touches sources) |

`.sw-log/` is git-ignored — it is machine-local data.

### Directory selection (which device)

`scan_photos.py` supports multiple devices/directories:

- **Explicit**: `--photos-dir /path/to/Photos` scans only that directory (and
  remembers it, even without a commit).
- **Auto (default)**: scans every directory remembered in `state.json` that is
  *currently mounted/present*, plus the default seed dir from
  `config.photos_dir`. Absent/not-mounted remembered dirs are reported as
  `skipped (not mounted)` — not an error. Exit 2 only if *no* directory is
  mounted at all.
- **Inspect**: `--list-dirs` prints each remembered directory with
  `present/absent`, its last-scan time, and its processed count.

## Steps

### 1. Scan

```
python3 .agents/skills/sw-log/scripts/scan_photos.py --data-dir .sw-log          # auto: all present devices
python3 .agents/skills/sw-log/scripts/scan_photos.py -P /path/to/Photos [--since "2026-09-06T00:00:00"]
```

- First run on a fresh device lists *everything* (large); later runs only new
  files (per-directory `processed` set).
- Exit 2 = requested directory missing, or no mounted directory found.
- Output: prints `dir/name capture=... src=... black=...` for each candidate and
  writes `.sw-log/pending.json`. `resolve_station.py` labels output with the
  same `dir/name` prefix.

### 2. Read the photos (agent vision)

For each `pending.json` record that is not trivially black:

```
python3 .agents/skills/sw-log/scripts/recognize.py <ORIGINAL> --data-dir .sw-log
```

view the original + enhanced variants, read band + frequency, then edit
`pending.json` in place:

```json
{ "band": "SW", "frequency_khz": 9410, "recognized": true, "notes": [] }
```

Convert to kHz (see `references/reading-displays.md`). For FM the band plan says
the rest; for AM/SW the resolver needs the frequency only.

### 3. Resolve stations

```
python3 .agents/skills/sw-log/scripts/resolve_station.py --data-dir .sw-log
```

- Reads annotated `pending.json`, queries EiBi (+ short-wave.info for SW,
  + MWLIST for AM), filters by capture UTC and weekday, sorts by distance from
  the configured location, writes `.sw-log/resolved.json`.
- FM has no database → candidates empty.

### 4. Determine the station (agent judgement)

Pick the most plausible candidate per capture and record the reasoning in
`notes`. Cross-checks in `references/databases.md`, plausibility by distance
and propagation (default location: Paris, France 48.8566 N 2.3522 E).

Unresolvable captures (no candidate, tiny time mismatch, FM local) → log the
frequency/time with `"station": null`, keep a note.

### 5. Produce the summary table + CSV

Render a markdown table (all times UTC):

| date (UTC) | time (UTC) | freq | band | station | location | notes |
|---|---|---|---|---|---|---|

`freq` shown as human-friendly (e.g. `9410 kHz`, `94.3 MHz`, `954 kHz`).
`location` = configured listening site (Paris, France).

Append the same rows to `.sw-log/sw-log.csv` (header kept once):

```
date_utc,time_utc,freq_khz,band,station,location,notes
```

### 6. Commit

```
python3 .agents/skills/sw-log/scripts/scan_photos.py --data-dir .sw-log --commit
```

Marks the pending files processed in `state.json` — routed to each record's
directory, each directory updating its own `processed` and `last_mtime` — so the
next scan starts fresh per device.

## Operational notes

- **Interruptability**: every step is idempotent (scan reappends only new files,
  resolve rewrites `resolved.json`, commit updates state). Re-running the same
  step is safe.
- **Per-device offset**: the timestamp offset is per directory via
  `config.directories` (see `references/timestamps.md`). The basic phone's -4h
  is **confirmed** (phone UI check); a smartphone used with USB may differ —
  calibrate it once with `scan_photos.py --calibrate <file>
  --real-local "YYYY-MM-DD HH:MM:SS" --apply` while that device is connected.
- **EiBi season drift**: eibi.py auto-selects by validity window, but the *next
  season's* file may 404 until published; `--refresh` re-downloads.
- **short-wave.info flakiness**: a failed fetch is logged, not fatal; EiBi alone
  is sufficient for SW when it happens.
- **--since vs --commit**: `--since` filters a scan query; `--commit` only
  affects state. A committed file disappears from future scans even without
  --since.
- Never edit source photos. Never commit `.sw-log/`.