"""Validate a DagloTranscriptCollector output zip for likely bogus transcript files."""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

BAD_MARKERS = [
    "Python virtual environment preparing",
    "Installing dependencies",
    "Requirement already satisfied",
    "Collecting pip",
    "Using cached pip",
    "Successfully installed pip",
    "PyInstaller",
    "No matching distribution found",
    "Building Windows EXE",
    "Downloading playwright",
    "ERROR: Could not find a version",
]


def check_text(text: str, min_chars: int = 500):
    text = text or ""
    compact = re.sub(r"\s+", "", text)
    if any(m in text for m in BAD_MARKERS):
        return False, "build/install log detected"
    if len(compact) < min_chars:
        return False, f"too short: {len(compact)} chars"
    hangul = len(re.findall(r"[가-힣]", text))
    timestamps = len(re.findall(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", text))
    if hangul < 80:
        return False, f"too little Korean text: {hangul} chars"
    if timestamps < 1:
        return False, "no transcript timestamps"
    return True, "ok"


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tools/validate_output_zip.py <daglo_output.zip>")
        return 2
    zip_path = Path(sys.argv[1])
    if not zip_path.exists():
        print(f"File not found: {zip_path}")
        return 2

    total = 0
    ok_count = 0
    bad = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".txt"):
                continue
            total += 1
            text = zf.read(name).decode("utf-8", errors="replace")
            ok, reason = check_text(text)
            if ok:
                ok_count += 1
            else:
                bad.append((name, reason))

    print(f"Total txt: {total}")
    print(f"Looks OK : {ok_count}")
    print(f"Suspicious: {len(bad)}")
    if bad:
        print("\nSuspicious files:")
        for name, reason in bad[:200]:
            print(f"- {name} :: {reason}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
