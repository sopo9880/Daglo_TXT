"""Offline helper: inspect a saved Daglo HTML source and list folder/board URLs.
Usage:
  python tools/inspect_daglo_html.py "붙여넣은 텍스트 (1).txt"
"""
from __future__ import annotations
import re
import sys
import urllib.parse
from pathlib import Path


def main(path: str):
    s = Path(path).read_text(encoding="utf-8", errors="ignore")
    boards = []
    for m in re.finditer(r"https%3A%2F%2Fdaglo\.ai%2Fboard%2F[^&\"'<>\s]+", s):
        url = urllib.parse.unquote(m.group(0))
        if "fileMetaId=" in url and url not in boards:
            boards.append(url)
    folders = sorted(set(re.findall(r"folderIds%3D([^%&]+)%26checkedFolder%3D([^&]+)", s)))
    print(f"folders: {len(folders)}")
    for a, b in folders:
        print(f"  {a}")
    print(f"boards: {len(boards)}")
    for u in boards:
        print(f"  {u}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tools/inspect_daglo_html.py <html.txt>")
        raise SystemExit(2)
    main(sys.argv[1])
