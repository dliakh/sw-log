# sw-log: CSV hygiene & transmitter-caching plan

Date: 2026-09-15

This documents the data-quality problems found in `.sw-log/sw-log.csv`, what was
fixed, and the remaining/optional improvements. It is the living plan the
requester asked for.

---

## 1. The `location` column spec (root of several issues)

Two different things were being conflated as "location":

| Where | Should hold | Current status |
|---|---|---|
| `config.json` → `location` | **observer / listener** (Paris, France) | correct |
| report tables / `sw-log.csv` → `location` column | **transmitter site** (where the signal comes from) | **fixed** |

For SW the signal arrives by skywave from transmitters worldwide, so the column
holds the transmitter's country/relay site (e.g. `Germany (Weenermoor)`,
`UK (Woofferton)`, `China`). For AM it is the local MW transmitter city/country.
The observer (Paris) must **not** appear in the `location` column.

This was fixed for all 34 rows that previously said `Paris` (all were SW rows
from 2026-09-11). Sites were taken from the transmitter cache
(`transmitters.json`, per-frequency site + power + distance + azimuth) plus two
user-supplied corrections (6070 → Rohrbach-Waal, Germany; 9460 → Emirler,
Turkey). Genuinely unresolved captures now show `—` instead of `Paris`.

Documented in `references/workflow.md` (step 5) and `references/databases.md`.

---

## 2. Problems found in `sw-log.csv` and fixes

| # | Problem | Cause | Fix |
|---|---|---|---|
| 1 | **Two header rows** at top | CSV rewritten by one writer while another had already written a header | Single header now; `normalize_csv.py` drops any leading header line(s) |
| 2 | **Mixed units** in `Frequency` (`5910 kHz` vs `531`) | hand-made rows vs script-generated rows | Strip `kHz`, store a bare number, column renamed `Frequency (kHz)` |
| 3 | **Duplicate row** (`2026-09-09 19:58 6930`) | row appended twice | Exact-duplicate rows removed (243 → 242 data rows) |
| 4 | **Misaligned columns** — `WMLK (Bethel, PA)` split across `Station`/`Location` | an **unquoted comma** in a field; the trailing quoted `Notes` field masked it as 7 columns | Repaired: `Station = WMLK (Bethel, PA)`, `Location = USA (Bethel, PA)` |
| 5 | **Observer in `location`** (`Paris`) | spec was ambiguous | Resolve to transmitter site (see §1) |
| 6 | **Unsorted** | rows appended in capture order per session | Sorted ascending by `(Date, Time, Frequency)` |

### Why the comma bug is subtle
A row like
`…,SW,WMLK (Bethel, PA),"SW 17.525; 17525, 17:30-22:30"`
parses to exactly 7 CSV fields (the trailing quoted `Notes` absorbs one comma),
so naive column-count checks pass. The tell is a **US-state fragment** left in the
`Location` column (e.g. `PA)`). `normalize_csv.py` detects this pattern
(`^\s*[A-Z]{2}\s*\)\s*$` against a known state) and rejoins the field.

---

## 3. Transmitter data caching (new)

short-wave.info / MWLIST transmitter data (site lat/lon, power, azimuth) changes
rarely, so `resolve_station.py` now caches each scraped response keyed by
frequency in `<data-dir>/transmitter-cache.json`
(`--sw-cache-ttl`, default 7 days). A fresh entry is reused instead of
re-fetching — this also avoids HTTP 429 rate-limiting when the same frequency is
scanned across sessions. Verified: a second run reuses the cache with no network
call.

This cache backs the `location` = transmitter-site resolution. A separate,
derived cache of unique sites (`transmitters.json`, `am-transmitters.json`)
carries distance/azimuth from the observer.

---

## 4. Preventing recurrence (code / process)

- **Always write the CSV with `csv.writer`** — it quotes any field containing a
  comma. Both writers that touch `sw-log.csv` (`build_table.py`,
  `append_sept13.py`) already do this; the misaligned row came from a manual
  edit, not a script. (Documented in `references/workflow.md`.)
- **`normalize_csv.py`** (`.sw-log/normalize_csv.py`) is a one-shot normalizer:
  single header, strip `kHz`, repair unquoted-comma rows, replace `Paris`,
  dedup, sort. Re-run it after any manual edit to a CSV. (It is in `.sw-log/`,
  git-ignored, like all runtime state.)
- **Single source of truth for the header**: the header lives in
  `normalize_csv.py` / `build_table.py`; keep them identical.

---

## 5. Optional future work (not done)

1. **Auto-populate `location` from the resolver.** Currently the agent builds the
   `location` column by hand. The resolver already computes per-candidate
   `distance_km` and `site` (transmitter lat/lon); a downstream step could set
   `location` to the top candidate's transmitter site automatically. Worth doing
   once the multi-site ambiguity (e.g. KBS from Woofferton vs Korea) is handled —
   the resolver would need the broadcast target/time to pick the right site.
2. **Cache TTL tuning / invalidation.** 7 days is a default; SW schedules are
   seasonal (EiBi seasons). Consider clearing the cache at season change.
3. **`normalize_csv.py` → a guarded library function** the agent calls before any
   CSV write, so normalization is enforced rather than ad hoc.

---

## 6. Verification (2026-09-15)

`.sw-log/sw-log.csv` after normalization:
- 1 header (`Date (UTC),Time (UTC),Frequency (kHz),Band,Station,Location,Notes`)
- 242 data rows; sorted by (Date, Time, Frequency)
- 0 rows with `kHz` in the frequency cell; 0 misaligned (round-trip check)
- 0 rows with `Paris`; 1 duplicate removed; `WMLK (Bethel, PA)` repaired
- Backup: `.sw-log/sw-log.csv.bak.*` (timestamped)
