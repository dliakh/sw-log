#!/usr/bin/env python3
"""Find new radio-display photos across one or more photo directories.

Pipeline stage 1 of the sw-log skill.

Supports multiple devices/directories:
  * --photos-dir DIR scans one specific directory (also remembers it).
  * Without --photos-dir, auto-scans every directory remembered from previous
    runs (state.json) that is currently mounted/present. Each directory keeps
    its own processed-file set and last-scan time. The timestamp calibration
    (the phone's double-shift offset) is remembered per directory in config.

Pipeline stage 2/3 see recognize.py / resolve_station.py.

Timestamp sources, in priority order:
  1. EXIF DateTimeOriginal / CreateDate (interpreted as local wall clock
     in the configured timezone, converted to UTC)
  2. Filename as a Unix Epoch-ms integer (e.g. "1696151323591.jpg")
  3. Filesystem mtime + per-directory offset (default -4h for the basic phone)

Usage:
  python3 scan_photos.py [--data-dir .sw-log]                # auto: mounted, remembered dirs
  python3 scan_photos.py --photos-dir DIR [--since ISO]      # scan one directory
  python3 scan_photos.py --list-dirs                          # list remembered dirs
  python3 scan_photos.py --commit                             # mark the pending list processed
  python3 scan_photos.py --calibrate FILE --real-local "YYYY-MM-DD HH:MM:SS" [--apply]

Exit codes: 0 ok, 2 no directory to scan (missing when explicitly requested,
or no remembered directory currently mounted).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_PHOTOS_DIR = "/media/dlyakh/FD2B-F90B/Photos"
DEFAULT_TZ = "Europe/Paris"
DEFAULT_OFFSET_HOURS = -4.0
DEFAULT_BLACK_THRESHOLD = 0.05

CONFIG_DEFAULTS = {
    "photos_dir": DEFAULT_PHOTOS_DIR,      # default seed directory (fallback only)
    "local_tz": DEFAULT_TZ,
    "location": {"name": "Paris, France", "lat": 48.8566, "lon": 2.3522},
    "mtime_to_utc_offset_hours": DEFAULT_OFFSET_HOURS,
    "black_threshold": DEFAULT_BLACK_THRESHOLD,
    # per-directory overrides, e.g.:
    # "directories": {"/media/...": {"mtime_to_utc_offset_hours": -4.0, "local_tz": "Europe/Paris", "black_threshold": 0.05}}
    "directories": {},
}

STATE_VERSION = 2

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

DEVICE_OVERRIDE_KEYS = ("mtime_to_utc_offset_hours", "local_tz", "black_threshold")


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def load_json(path: Path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def get_local_tz(name: str) -> dt.tzinfo:
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Unknown timezone {name!r}: {exc}")


def dir_config(config: dict, path: str) -> dict:
    """Config for a specific directory: global defaults + per-dir overrides."""
    merged = dict(config)
    overrides = config.get("directories", {}).get(path)
    if isinstance(overrides, dict):
        for key in DEVICE_OVERRIDE_KEYS:
            if key in overrides:
                merged[key] = overrides[key]
    return merged


def migrate_state_v1(state: dict, default_dir: str) -> dict:
    """One-time migration of the legacy single-directory state format."""
    if isinstance(state, dict) and "directories" not in state:
        legacy = {"version": STATE_VERSION, "directories": {}}
        processed = state.get("processed", [])
        last_mtime = float(state.get("last_mtime", 0.0))
        if processed or last_mtime:
            legacy["directories"][default_dir] = {"processed": list(processed), "last_mtime": last_mtime}
        return legacy
    if isinstance(state, dict) and "directories" in state:
        state.setdefault("version", STATE_VERSION)
        return state
    return {"version": STATE_VERSION, "directories": {}}


def exif_capture(path: Path, tz_name: str) -> str | None:
    """Return an ISO UTC wall time if the file carries EXIF capture tags."""
    exiftool = shutil.which("exiftool")
    if not exiftool:
        return None
    try:
        out = subprocess.run(
            [exiftool, "-s3", "-d", "%Y-%m-%d %H:%M:%S",
             "-DateTimeOriginal", "-CreateDate", str(path)],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    offset_raw = ""
    try:
        offset_raw = subprocess.run(
            [exiftool, "-s3", "-OffsetTimeOriginal", str(path)],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    if not out:
        return None
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    raw = lines[0]
    try:
        naive = dt.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if offset_raw:
        m = re.match(r"([+-])(\d{2}):?(\d{2})", offset_raw)
        if m:
            off = dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
            if m.group(1) == "-":
                off = -off
            tz = dt.timezone(off)
            return naive.replace(tzinfo=tz).astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    local = get_local_tz(tz_name)
    return naive.replace(tzinfo=local).astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def epoch_ms_from_name(name: str) -> float | None:
    m = re.fullmatch(r"1[0-9]{12}\.(?:jpg|jpeg|png)", name, re.I)
    if not m:
        return None
    return int(name.rsplit(".", 1)[0]) / 1000.0


def brightness_metrics(path: Path) -> tuple[float | None, float | None]:
    identify = shutil.which("identify")
    if not identify:
        return None, None
    try:
        out = subprocess.run(
            [identify, "-format", "%[fx:mean]|%[fx:standard_deviation]", str(path)],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None, None
    try:
        mean, std = out.split("|")
        return float(mean), float(std)
    except ValueError:
        return None, None


def to_utc_iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat(timespec="seconds")


def dir_has_images(path: Path) -> bool:
    """True if the directory contains at least one directly-contained image.

    Used to distinguish a real photo directory from a mount root that only
    holds a Photos/ subfolder (e.g. a phone drive): such a root has no images
    of its own and must not be reported as a mounted photo directory.
    """
    try:
        return any(p.suffix.lower() in IMAGE_EXTS for p in path.iterdir())
    except OSError:
        return False


def describe(record: dict, parent: str) -> None:
    rel = os.path.join(os.path.basename(parent), record["name"])
    print(f"  {rel:36s} capture={record['capture_utc']}  "
          f"src={record['time_source']:<14s} black={record['black']}")


def list_dirs(state: dict, config: dict) -> int:
    dirs = state.get("directories", {})
    if not dirs and config.get("photos_dir"):
        dirs = {config["photos_dir"]: {"processed": [], "last_mtime": 0.0}}
    if not dirs:
        print("(no directories remembered yet; scan one with --photos-dir)")
        return 0
    for path, info in sorted(dirs.items()):
        present = Path(path).is_dir()
        last_mtime = float(info.get("last_mtime", 0.0))
        last_scan = to_utc_iso(last_mtime) if last_mtime else "never"
        nproc = len(info.get("processed", []))
        print(f"{'present' if present else 'absent ':7s} {path}".ljust(64)
              + f"last_scan={last_scan} processed={nproc}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-P", "--photos-dir", help="scan only this directory (default: auto-detect mounted, remembered dirs)")
    ap.add_argument("--data-dir", default=".sw-log", help="project-local data dir (state, config, work)")
    ap.add_argument("--black-threshold", type=float, help="mean-brightness under which a photo is treated as black")
    ap.add_argument("--since", help="only files whose mtime >= this ISO instant (e.g. 2026-09-09T00:00:00)")
    ap.add_argument("--commit", action="store_true", help="mark the current pending list as processed")
    ap.add_argument("--list-dirs", "--ls", action="store_true", help="list remembered directories and exit")
    ap.add_argument("--calibrate", metavar="FILE", help="print offset implied by --real-local for one file")
    ap.add_argument("--real-local", metavar="YYYY-MM-DD HH:MM:SS", help="true capture time as local wall clock (calibrate)")
    ap.add_argument("--apply", action="store_true", help="with --calibrate: save the offset into config for that file's directory")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).resolve()
    config_path = data_dir / "config.json"
    state_path = data_dir / "state.json"
    pending_path = data_dir / "pending.json"

    config = dict(CONFIG_DEFAULTS)
    config.update(load_json(config_path))
    config.setdefault("directories", {})
    if args.black_threshold:
        config["black_threshold"] = args.black_threshold
    save_json(config_path, config)

    default_dir = str(Path(config.get("photos_dir", DEFAULT_PHOTOS_DIR)).resolve())
    state = migrate_state_v1(load_json(state_path), default_dir)

    if args.list_dirs:
        return list_dirs(state, config)

    pending_records = load_json(pending_path).get("records", [])

    photos_dir = None
    if args.calibrate:
        path = Path(args.calibrate).resolve()
        if not args.real_local:
            ap.error("--calibrate requires --real-local")
        if not path.is_file():
            log(f"[scan] calibrate file not found: {path}")
            return 2
        parent = str(path.parent.resolve())
        dcfg = dir_config(config, parent)
        mtime = os.stat(path).st_mtime
        real_utc = dt.datetime.strptime(args.real_local, "%Y-%m-%d %H:%M:%S")
        real_utc = real_utc.replace(tzinfo=get_local_tz(dcfg["local_tz"])).timestamp()
        implied = real_utc - mtime
        print(f"file={parent}")
        print(f"mtime_epoch={mtime:.0f}")
        print(f"implied mtime_to_utc_offset_hours = {implied / 3600:+.3f}")
        if args.apply:
            config["directories"].setdefault(parent, {})
            config["directories"][parent] = dict(config["directories"][parent])
            config["directories"][parent]["mtime_to_utc_offset_hours"] = round(implied / 3600, 3)
            save_json(config_path, config)
            print(f"-> saved to config directories[{parent}].mtime_to_utc_offset_hours")
            print("-> re-run a scan of that directory to use the new offset")
        else:
            print("-> use --apply to save it into config for this directory")
        return 0

    if args.commit:
        state.setdefault("directories", {})
        by_dir: dict[str, list] = {}
        for rec in pending_records:
            d = rec.get("directory") or default_dir
            by_dir.setdefault(d, []).append(rec)
        for d, recs in sorted(by_dir.items()):
            entry = state["directories"].setdefault(d, {"processed": [], "last_mtime": 0.0})
            entry["processed"] = list(entry.get("processed", []))
            for rec in recs:
                f = rec.get("file")
                if f and f not in entry["processed"]:
                    entry["processed"].append(f)
            newest = max((r.get("mtime_epoch", 0.0) for r in recs), default=0.0)
            entry["last_mtime"] = max(float(entry.get("last_mtime", 0.0)), newest)
        save_json(state_path, state)
        log(f"[scan] committed {len(pending_records)} file(s) across {len(by_dir)} directorie(s) to state")
        return 0

    since_epoch = None
    if args.since:
        try:
            since_epoch = dt.datetime.fromisoformat(args.since).timestamp()
        except ValueError:
            ap.error(f"cannot parse --since {args.since!r}")

    if args.photos_dir:
        pd = Path(args.photos_dir).resolve()
        if not pd.is_dir():
            log(f"[scan] photos dir not present: {pd} (requested via --photos-dir)")
            return 2
        targets = [pd]
        if args.since is None:
            log(f"[scan] scanning {pd}")
    else:
        remembered = [Path(d) for d in state.get("directories", {})]
        seed = Path(default_dir)
        if all(d != seed for d in remembered):
            remembered.append(seed)
        absent = [str(d) for d in remembered if not d.is_dir()]
        # Only directories that actually contain images count as photo
        # directories; a mount root that holds only a Photos/ subfolder has no
        # images of its own and is excluded here (and dropped from state below).
        targets = [d for d in remembered if d.is_dir() and dir_has_images(d)]
        for a in absent:
            log(f"[scan] skipped (not mounted): {a}")
        if not targets:
            log(f"[scan] no remembered photo directory is currently mounted; nothing to scan")
            log(f"[scan] remembered dirs: {', '.join(map(str, remembered)) or none}")
            return 2
        if len(targets) > 1:
            log(f"[scan] {len(targets)} directories mounted: " + ", ".join(map(str, targets)))
        elif not state.get("directories"):
            log(f"[scan] no directories remembered yet; using default {targets[0]}")

    all_records = []
    state.setdefault("directories", {})
    for photos_dir in targets:
        dcfg = dir_config(config, str(photos_dir))
        entry = state["directories"].setdefault(str(photos_dir), {"processed": [], "last_mtime": 0.0})
        entry["processed"] = list(entry.get("processed", []))
        processed = set(entry["processed"])
        dir_newest = float(entry.get("last_mtime", 0.0))

        images = sorted(
            (p for p in photos_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS),
            key=lambda p: (p.stat().st_mtime, p.name),
        )

        if not images:
            # A directory with no directly-contained images is not a photo
            # directory (e.g. a phone-drive root that only holds a Photos/
            # subfolder). Never remember it, so pointing -P/--photos-dir at a
            # parent path does not pollute future auto-scans.
            state["directories"].pop(str(photos_dir), None)
            log(f"[scan] skipped (no images directly in dir): {photos_dir}")
            continue

        new_records = []
        for path in images:
            try:
                st = path.stat()
            except OSError:
                continue
            dir_newest = max(dir_newest, st.st_mtime)
            name = path.name
            if str(path) in processed:
                continue
            if since_epoch is not None and st.st_mtime < since_epoch:
                continue

            capture_utc = None
            time_source = None

            exif_utc = exif_capture(path, dcfg["local_tz"])
            if exif_utc:
                capture_utc = exif_utc
                time_source = "exif"
            else:
                ms_epoch = epoch_ms_from_name(name)
                if ms_epoch is not None:
                    capture_utc = to_utc_iso(ms_epoch)
                    time_source = "filename-epoch-ms"
                else:
                    corr = float(dcfg.get("mtime_to_utc_offset_hours", DEFAULT_OFFSET_HOURS))
                    capture_utc = to_utc_iso(st.st_mtime + corr * 3600.0)
                    time_source = "mtime+offset"

            mean, std = brightness_metrics(path)
            record = {
                "file": str(path),
                "name": name,
                "directory": str(photos_dir),
                "mtime_epoch": st.st_mtime,
                "mtime_local": dt.datetime.fromtimestamp(st.st_mtime, get_local_tz(dcfg["local_tz"])).isoformat(timespec="seconds"),
                "capture_utc": capture_utc,
                "time_source": time_source,
                "mean_brightness": mean,
                "std_brightness": std,
                "black": bool(
                    mean is not None
                    and std is not None
                    and mean < float(dcfg.get("black_threshold", DEFAULT_BLACK_THRESHOLD))
                ),
                "status": "new",
                "frequency_khz": None,
                "band": None,
                "recognized": False,
                "notes": [],
            }
            new_records.append(record)
            describe(record, str(photos_dir))

        all_records.extend(new_records)
        entry["last_mtime"] = dir_newest

    save_json(pending_path, {"records": all_records})
    save_json(state_path, state)
    log(f"[scan] {len(all_records)} new photo(s) listed in {pending_path} "
        f"across {len(targets)} directorie(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())