# Timestamps, timezones, and the "why is it 4 hours off" bug

## Background

The phone writes no EXIF capture time; the files only carry filesystem mtime.
For the Sept 9 2026 batch the user recalls taking the photos ~20:00 local
(Europe/Paris). The displayed filesystem mtime is `2026-09-09 23:58 +02:00`
(≈ 21:58 UTC). That implies the phone's local time is being **double-shifted**:

```
real capture (20:00 Paris = 18:00 UTC)
  + phone stored a wrong +2h offset
  + the PC adds PST→local again when displaying/marking mtime
  ⇒ mtime shows ~21:58-23:58 'local'
```

So `mtime` ≈ capture time **plus 4 hours**. The offset is expressed as
`mtime_to_utc_offset_hours` (hours to subtract from mtime to get true UTC):

```
capture_utc_epoch = mtime_epoch + mtime_to_utc_offset_hours * 3600
```

The default in `config.json` is **`-4.0`** for the basic phone, which puts the
Sept 9 batch at ≈ 17:14–17:58 UTC (= 19:14–19:58 Paris). **This was confirmed
(2026-09-10) against the phone's own UI**: for `DSC_0000250.jpg` (mtime stored
21:31:58 UTC) the phone's file browser shows **21:31 "local"**, i.e. the phone
clock is ~+2h fast, so the true capture is 19:31 Paris = 17:31 UTC — exactly
what the −4h rule computes. `--calibrate` on that file yields `-4.000` (the
`-4.016` with a minute-rounded `--real-local` is just 58 s of truncation).
Per-device, the offset is stored in `config.directories[<path>]`; the basic
phone is pinned to -4.0.

## Where each timestamp source sits in priority

`scan_photos.py` computes `capture_utc` and records `time_source`:

1. **EXIF** `DateTimeOriginal`/`CreateDate` — interpreted as *local wall clock*
   in the directory's `local_tz` (default `Europe/Paris`), converted with the
   EXIF `OffsetTimeOriginal` if present. `time_source = "exif"`.
2. **Filename epoch-ms** — names like `1696151323591.jpg`; interpreted as a
   real Unix epoch in milliseconds (already UTC). `time_source = "filename-epoch-ms"`.
3. **mtime + offset** — `time_source = "mtime+offset"` (applies the directory's
   `mtime_to_utc_offset_hours`, default -4h).

## Calibrating the offset (do this once per device)

The offset is **phone/device-specific** (a smartphone connected over USB may
double-shift differently), so it is stored per directory. The basic phone's
`-4.0` is already **confirmed** (see Background); calibrate only new devices:

```json
{ "directories": { "/media/dlyakh/FD2B-F90B/Photos": { "mtime_to_utc_offset_hours": -4.0 } } }
```

To nail it for the currently-connected device:

```
python3 .agents/skills/sw-log/scripts/scan_photos.py \
    --calibrate /media/dlyakh/FD2B-F90B/Photos/DSC_0000234.jpg \
    --real-local "2026-09-09 19:00:00" \
    --apply --data-dir .sw-log
```

- `--real-local` = the true capture instant as wall clock in the device's
  `local_tz` (default Europe/Paris).
- The command prints `implied mtime_to_utc_offset_hours = -4.000`; with
  `--apply` it writes that value into `config.directories[<photo dir>]` and a
  re-scan of that directory picks it up. Without `--apply` it just prints.
- Run calibration while each device is connected; different devices get their
  own entry (a device-specific `--real-local` time comes from the user's memory
  of when the photos were taken).

## Practical rules

- Weekday/day-of-week used for schedule matching comes from `capture_utc`
  (UTC). EiBi and short-wave.info schedules are UTC-based; do not convert.
- Local-time display for the final table *may* be shown in `local_tz`
  (Europe/Paris) for readability, but all matching and storing is UTC.
- If a future scan shows `time_source="exif"` for a *different* phone with sane
  EXIF, trust it (it does not suffer the double-shift) — mix of sources is fine.