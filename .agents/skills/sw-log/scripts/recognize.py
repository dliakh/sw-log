#!/usr/bin/env python3
"""Prepare radio-display photos for reading and emit an OCR hint.

Pipeline stage 2 of the sw-log skill.

Low-res 640x480 phone photos of an LCD display do NOT OCR well with generic
tesseract (verified empirically: output is noise for both dark and bright
photos). The reliable reader is the language model's own vision. This script:

  * never modifies the source file (all work happens on copies in the work dir)
  * writes a few enhanced variants so the model can pick the most legible one
  * runs tesseract with a digits/units whitelist and reports the raw hint so
    the model can cross-check its own reading (treat hints as LOW confidence)

Usage:
  python3 recognize.py FILE... [--data-dir DIR] [--work-dir DIR] [--scale N]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

VARIANTS = {
    "normalized": ("-colorspace Gray -resize {scale} -normalize", "_norm"),
    "stretched": ("-colorspace Gray -resize {scale} -auto-level -gamma 2", "_stretch"),
    "threshold": ("-colorspace Gray -resize {scale} -normalize -lat 25x25+5%", "_lat"),
}
OCR_WHITELIST = "0123456789.kMHzAMFWSW"


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except (subprocess.SubprocessError, OSError) as exc:
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": str(exc)})()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("images", nargs="+", help="photo file(s) to prepare")
    ap.add_argument("--data-dir", default=".sw-log", help="project-local data dir")
    ap.add_argument("--work-dir", help="where to write enhanced variants (default <data-dir>/work)")
    ap.add_argument("--scale", default="400%", help="resize factor for variants (default 400%%)")
    args = ap.parse_args()

    work_dir = Path(args.work_dir or (Path(args.data_dir).resolve() / "work")).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    for img in args.images:
        src = Path(img)
        if not src.is_file():
            print(f"[recognize] missing {src}", file=sys.stderr)
            continue

        stem = src.stem[:40]
        variants = []
        for name, (recipe, suffix) in VARIANTS.items():
            cmd = ["convert", str(src)] + recipe.format(scale=args.scale).split() + [str(work_dir / f"{stem}{suffix}.png")]
            done = run(cmd)
            out = work_dir / f"{stem}{suffix}.png"
            if done.returncode == 0 and out.exists():
                variants.append(str(out))

        ocr_hint = ""
        if variants and shutil.which("tesseract"):
            kept = " ".join(["tesseract", variants[0], "stdout",
                             "--psm", "6", "-c", f"tessedit_char_whitelist={OCR_WHITELIST}"])
            done = run(kept.split())
            if done.returncode == 0:
                ocr_hint = " ".join(done.stdout.split())

        print(json.dumps({
            "file": str(src),
            "variants": variants,
            "ocr_hint": ocr_hint,
            "ocr_confidence": "low",
        }, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())