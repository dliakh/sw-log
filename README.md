# sw-log — phone-photo radio log

Turn phone photos of a radio's LCD frequency display (SW/AM/FM) into a
listening log: *when* you were tuned, *where*, to **which frequency**, and
**which broadcast station** was transmitting at that moment.

The user snaps photos of the receiver's display with a phone, mounts the phone
over USB, and runs the `sw-log` skill. Photo mtime (plus a per-device
calibration offset) becomes the UTC capture time; vision reads the display;
schedule databases resolve the frequency+time to a station.

## Pipeline

| stage | script | does |
|---|---|---|
| 1. scan | `scan_photos.py` | list new photos, compute UTC capture time, flag screen-off/dark, per-device calibration, multi-device state |
| 2. recognize | `recognize.py` | enhance photos (`norm`/`stretch`/`lat` variants) + low-confidence OCR hint |
| 3. eibi | `eibi.py` | season-aware EiBi shortwave schedule cache |
| 4. resolve | `resolve_station.py` | EiBi + short-wave.info + MWLIST matching, time/day filtering, distance ranking |

The actual frequency/band reading is done by the agent's (or a local vision
model's) image vision — generic OCR is too weak for dark off-angle LCD shots.

## Quick start

```
python3 .agents/skills/sw-log/scripts/scan_photos.py --data-dir .sw-log --since "2026-09-09T00:00:00+00:00"
# read the pending photos (vision), annotate band + frequency_khz in .sw-log/pending.json
python3 .agents/skills/sw-log/scripts/resolve_station.py --data-dir .sw-log
python3 .agents/skills/sw-log/scripts/scan_photos.py --data-dir .sw-log --since "2026-09-09T00:00:00+00:00" --commit
```

- Log rows are appended to `.sw-log/sw-log.csv`; the log/state is git-ignored.
- Timestamps: per-device `mtime_to_utc_offset_hours` in `.sw-log/config.json`
  (`--calibrate FILE --real-local "…" --apply`), default `-4.0` for the basic
  phone (confirmed via the phone's own UI).
- Receiver quirk baked into the docs: SW is displayed in **MHz**, AM in **kHz**,
  with a smaller trailing digit = 5 kHz tuning step.

## Layout

- `.agents/skills/sw-log/` — the opencode skill: `SKILL.md`, `references/`
  (databases, reading-displays, timestamps, workflow), `scripts/`
- `spec.md` — full design spec incl. multi-device support (§9)

Validated end-to-end on a Sept 9 2026 session: 17 photos read via a local vision
model, 13 shortwave + 2 AM captures resolved to stations (CRI, KBS World Radio,
Voice of Vietnam, CNR, …) and logged.