# Reading radio-display photos

## What the photos are

Low-resolution (**640x480 JPEG**) phone photos of small LCD screens — typically
black-on-grey segment/LCD digits showing the tuned frequency and band. From the
mount `/media/dlyakh/FD2B-F90B/Photos/` (a phone/disk). File names:
`DSC_0000252.jpg` (sequential) or an epoch-millis integer like
`1696151323591.jpg`.

## Why OCR is not the primary reader

Verified empirically (2026-09-09, 2026-09-10 samples):

- generic `tesseract` on these photos produces noise (`AM 0A A ASS 7 5 4 SH 4 .`
  on a bright photo; garbage on dark ones) — **never trust it for the value**;
- photos are shot at an angle, possibly out of focus, with moiré on LCD pixels;
- the phone's own metadata is absent (no EXIF `DateTimeOriginal`/`CreateDate` in
  any file tested).

The reliable reader is **the agent's own vision** when the image is attached by
the caller. The scripts exist only to (a) list what needs reading, (b) make the
image more legible, and (c) give a *low-confidence* OCR hint to cross-check.

## Workflow in one sentence

view the photo → read the frequency digits + unit (kHz/MHz) + band from the
display → write those into `pending.json` → let `resolve_station.py` find the
station.

## Viewing photos

- Run `python3 .agents/skills/sw-log/scripts/recognize.py <ORIGINAL> --data-dir .sw-log`.
  It produces enhanced copies in `.sw-log/work/<name>_{norm,stretch,lat}.png`
  (grayed, resized, normalised / auto-leveled / thresholded) and prints the
  tesseract hint. It never touches the source image.
- Read the original (and, if unclear, the `_lat` or `_stretch` variants).
- If the image is all-black (no legible display), mark it and move on. The
  `black:true` flag in `pending.json` is a *hint*, not a verdict — a dark photo
  can still be readable; use your eyes.

## What to extract

1. **Frequency**: a number on the display. Decide the unit from context:
   - `88.5`, `101.1`, `94.3` … decimal < ~108 → **MHz**.
   - `153`, `603`, `954`, `1062` … integer ~150–1710 → **kHz** (AM/MW).
   - `6095`, `7260`, `9410`, `11970`, `13670` … ≥ 2000 → **kHz** (SW).
   - `12780` etc. — SW in kHz; do NOT mistake a 4–5 digit SW frequency for
     MHz.
2. **Band**: `SW`, `AM` (sometimes shown `LW`/`MW`), or `FM`. If not shown,
   infer from frequency (see below).
3. Take the reading with a margin of trust: 640x480 LCD shots are ambiguous —
   `9` vs `8`, `5` vs `6`, decimal-point position. Note uncertainty in
   `notes`, e.g. `"freq uncertain: could be 9410"`.

## Frequency → band inference (used by resolve_station.py)

- `>= 60000` kHz  → FM (60–108 MHz as kHz)
- `150 <= f <= 1710` → AM (long/MW)
- `>= 2300` → SW
- otherwise → unknown (agent must decide)
- A number like `94.3` is *MHz* → store `frequency_khz = 94300`.

## Errors to avoid

- Recording `9410` MHz instead of kHz (SW frequencies are kHz).
- Recording `94.3` kHz instead of 94300 kHz for FM — always convert to kHz when
  writing `frequency_khz` (units field is not stored).
- Treating the OCR hint as truth; it is a tiebreaker at best.
- Photographing *more* than the display: crop mentally to the display when
  reading (segment LCDs stop early, e.g. only 3 of 5 digits light up).
- Taking a generic model's claim about display units at face value. Verified
  2026-09-10 on the basic receiver: it shows **SW in MHz** (e.g. `9.82`) and
  **AM in kHz** (`693`); it does NOT follow the "SW receivers display kHz"
  assumption, and the last SW digit can be a smaller 5 kHz tuning-step digit
  (on/off). A **band-plausibility** check catches misreads: after reading,
  confirm `band` vs `frequency_khz` are consistent (SW ≈ 1700–30000 kHz, AM/MW
  150–1710 kHz, FM 87.5–108 MHz as kHz). Dim backlight makes small digits
  disappear — keep the receiver's batteries charged.