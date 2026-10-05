"""Writable user data lives outside the application installation."""
import json
import os
import sys
from pathlib import Path


def resource_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def data_dir():
    override = os.environ.get("DAGLO_DATA_DIR")
    base = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DagloTXT"
    base.mkdir(parents=True, exist_ok=True)
    return base


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def configure_browser_runtime():
    bundled = resource_dir() / "browsers"
    if bundled.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)
