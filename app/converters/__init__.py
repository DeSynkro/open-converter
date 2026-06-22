from pathlib import Path

from ..storage import DATA_DIR

OUTPUT_DIR = DATA_DIR / "converted"

_conversions: dict[str, dict] = {}

def _mk_out():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def set_pending(file_id: str):
    _conversions[file_id] = {
        "status": "pending",
        "progress": 0,
        "output_path": None,
        "error": None,
    }

def set_running(file_id: str):
    if file_id in _conversions:
        _conversions[file_id]["status"] = "running"

def update_progress(file_id: str, pct: int):
    if file_id in _conversions:
        _conversions[file_id]["progress"] = max(0, min(pct, 100))

def set_done(file_id: str, output_path: str):
    if file_id in _conversions:
        _conversions[file_id]["status"] = "done"
        _conversions[file_id]["progress"] = 100
        _conversions[file_id]["output_path"] = output_path

def set_error(file_id: str, msg: str):
    if file_id in _conversions:
        _conversions[file_id]["status"] = "error"
        _conversions[file_id]["error"] = msg

def get_status(file_id: str) -> dict | None:
    return _conversions.get(file_id)

def get_all_statuses() -> dict[str, dict]:
    return dict(_conversions)

def clear_state(file_id: str | None = None):
    if file_id:
        _conversions.pop(file_id, None)
    else:
        _conversions.clear()

async def dispatch(file_id: str, entry: dict, settings: dict):
    set_pending(file_id)
    set_running(file_id)
    _mk_out()

    cat = entry.get("category", "")

    if cat == "image":
        from .image import convert as convert_image
        await convert_image(file_id, entry, settings)
    else:
        set_error(file_id, f"Converter for '{cat}' not yet implemented")
