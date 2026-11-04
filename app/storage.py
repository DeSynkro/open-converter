import json
from pathlib import Path

from .models import DEFAULT_SETTINGS

DATA_DIR = Path("/app/data")
UPLOADS_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "converted"
THUMBS_DIR = DATA_DIR / "thumbnails"
FILES_JSON = DATA_DIR / "files.json"
SETTINGS_JSON = DATA_DIR / "settings.json"
OVERRIDES_JSON = DATA_DIR / "overrides.json"


def ensure_dirs():
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)


def load_files() -> list[dict]:
    ensure_dirs()
    if not FILES_JSON.exists():
        return []
    try:
        return json.loads(FILES_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def save_files(files: list[dict]):
    ensure_dirs()
    FILES_JSON.write_text(json.dumps(files, indent=2))


def load_settings() -> dict:
    ensure_dirs()
    if not SETTINGS_JSON.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        stored = json.loads(SETTINGS_JSON.read_text())
        merged = dict(DEFAULT_SETTINGS)
        merged.update(stored)
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict):
    ensure_dirs()
    SETTINGS_JSON.write_text(json.dumps(settings, indent=2))


def load_overrides() -> dict:
    ensure_dirs()
    if not OVERRIDES_JSON.exists():
        return {}
    try:
        return json.loads(OVERRIDES_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_overrides(overrides: dict):
    ensure_dirs()
    OVERRIDES_JSON.write_text(json.dumps(overrides, indent=2))
