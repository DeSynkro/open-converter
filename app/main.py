import asyncio
import io
import shutil
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .converters import (
    OUTPUT_DIR as CONV_OUTPUT_DIR,
    clear_state as clear_conv_state,
    dispatch as start_conversion,
    get_all_statuses,
    get_status,
)
from .metadata import get_dimensions, get_extended_metadata, get_metadata
from .models import (
    AUDIO_FORMATS,
    EXT_TO_FORMAT,
    IMAGE_FORMATS,
    LOSSLESS_AUDIO,
    VIDEO_FORMATS,
    FileEntry,
    categorize,
    now_iso,
)
from .storage import (
    UPLOADS_DIR,
    load_files,
    load_overrides,
    load_settings,
    save_files,
    save_overrides,
    save_settings,
)

app = FastAPI()

templates_dir = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    files = load_files()
    overrides = load_overrides()
    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "files": files,
        "overrides": overrides,
    })


@app.post("/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    entries: list[dict] = load_files()
    overrides = load_overrides()
    html_parts: list[str] = []

    for f in files:
        if not f.filename:
            continue

        entry_id = uuid.uuid4().hex[:12]
        ext = Path(f.filename).suffix
        dest = UPLOADS_DIR / f"{entry_id}{ext}"

        content = await f.read()
        dest.write_bytes(content)

        entry = FileEntry(
            id=entry_id,
            filename=f.filename,
            size=len(content),
            mime_type=f.content_type or "application/octet-stream",
            category=categorize(f.content_type or ""),
            uploaded_at=now_iso(),
        )
        d = entry.model_dump()
        entries.append(d)
        has_ov = entry_id in overrides
        html_parts.append(_file_entry_html(d, has_ov))

    save_files(entries)
    return HTMLResponse("".join(html_parts))


@app.get("/files/{file_id}/metadata")
async def file_metadata(file_id: str, extended: bool = Query(False)):
    entries = load_files()
    entry = next((e for e in entries if e["id"] == file_id), None)
    if not entry:
        return HTMLResponse("<p>File not found</p>")

    ext = Path(entry["filename"]).suffix
    filepath = UPLOADS_DIR / f"{entry['id']}{ext}"
    if not filepath.exists():
        return HTMLResponse("<p>File not found on disk</p>")

    if extended:
        meta = get_extended_metadata(filepath, entry["mime_type"])
    else:
        meta = get_metadata(filepath, entry["mime_type"])

    if not meta:
        meta = {"Note": "No metadata available"}

    rows = "".join(
        f'<div class="metadata-row"><span class="metadata-label">{k}</span><span class="metadata-value">{v}</span></div>'
        for k, v in meta.items()
    )

    more_btn = ""
    if not extended:
        more_btn = '<button class="meta-more-btn" data-id="' + file_id + '">Show More Metadata</button>'

    html = f"""<div class="meta-modal-content">
  <div class="meta-modal-header">{entry["filename"]}</div>
  <div class="metadata-grid">{rows}</div>
  {more_btn}
</div>"""
    return HTMLResponse(html)


@app.delete("/files/{file_id}")
async def delete_file(file_id: str):
    entries = load_files()
    entry = next((e for e in entries if e["id"] == file_id), None)
    if not entry:
        return HTMLResponse("")

    entries = [e for e in entries if e["id"] != file_id]
    save_files(entries)

    ext = Path(entry["filename"]).suffix
    filepath = UPLOADS_DIR / f"{entry['id']}{ext}"
    if filepath.exists():
        filepath.unlink()

    overrides = load_overrides()
    overrides.pop(file_id, None)
    save_overrides(overrides)

    clear_conv_state(file_id)
    for out_path in CONV_OUTPUT_DIR.glob(f"{file_id}*"):
        out_path.unlink(missing_ok=True)

    return HTMLResponse("")


@app.get("/settings/group")
async def group_settings(category: str = "image"):
    settings = load_settings()
    cat_settings = settings.get(category, {})
    return HTMLResponse(_settings_form_html(category, cat_settings, is_group=True))


@app.post("/settings/group")
async def save_group_settings(request: Request):
    form = await request.form()
    category = form.get("category", "image")
    settings = load_settings()
    settings[category] = {
        "output_format": form.get("output_format", ""),
        "quality": int(form.get("quality", 80)),
        "crf": _int_or_none(form.get("crf")),
        "bitrate": form.get("bitrate") or None,
        "max_width": _int_or_none(form.get("max_width")),
        "max_height": _int_or_none(form.get("max_height")),
        "strip_metadata": form.get("strip_metadata") == "1",
        "preserve_transparency": form.get("preserve_transparency") == "1",
        "use_scale": form.get("use_scale") == "1",
        "scale_pct": _int_or_none(form.get("scale_pct")),
        "file_size_goal": form.get("file_size_goal") == "1",
        "file_size_value": _int_or_none(form.get("file_size_value")),
        "file_size_unit": form.get("file_size_unit", "MB"),
    }
    save_settings(settings)
    return HTMLResponse("")


@app.get("/files/{file_id}/settings")
async def file_settings(file_id: str):
    entries = load_files()
    entry = next((e for e in entries if e["id"] == file_id), None)
    if not entry:
        return HTMLResponse("<p>File not found</p>")

    overrides = load_overrides()
    override = overrides.get(file_id, {})
    cat = entry["category"]

    settings = load_settings()
    merged = dict(settings.get(cat, {}))
    if cat == "image":
        merged["output_format"] = EXT_TO_FORMAT.get(Path(entry["filename"]).suffix.lower(), "JPEG")
    if override.get("active"):
        for key in ("output_format", "quality", "crf", "bitrate", "max_width", "max_height", "aspect_locked", "strip_metadata", "preserve_transparency", "use_scale", "scale_pct", "file_size_goal", "file_size_value", "file_size_unit"):
            val = override.get(key)
            if val is not None:
                merged[key] = val

    ow, oh, duration_secs = None, None, None
    ext = Path(entry["filename"]).suffix
    filepath = UPLOADS_DIR / f"{entry['id']}{ext}"
    if filepath.exists():
        if cat in ("image", "video"):
            ow, oh = get_dimensions(filepath, entry["mime_type"])
        elif cat == "audio":
            info = await _get_audio_info(filepath)
            dur_str = info.get("duration")
            if dur_str:
                duration_secs = float(dur_str)
            if "bit_rate" in info and override.get("bitrate") is None:
                br_kbps = int(info["bit_rate"]) // 1000
                merged["bitrate"] = f"{br_kbps}k"
            if "sample_rate" in info and not override.get("sample_rate"):
                merged["sample_rate"] = info["sample_rate"]

        if cat == "audio" and not merged.get("sample_rate"):
            merged["sample_rate"] = "44100"

    return HTMLResponse(_settings_form_html(cat, merged, is_group=False, file_id=file_id, filename=entry["filename"], orig_w=ow, orig_h=oh, file_duration=duration_secs))


@app.post("/files/{file_id}/settings")
async def save_file_settings(file_id: str, request: Request):
    form = await request.form()
    overrides = load_overrides()
    overrides[file_id] = {
        "active": True,
        "output_format": form.get("output_format") or None,
        "quality": _int_or_none(form.get("quality")),
        "crf": _int_or_none(form.get("crf")),
        "bitrate": form.get("bitrate") or None,
        "max_width": _int_or_none(form.get("max_width")),
        "max_height": _int_or_none(form.get("max_height")),
        "aspect_locked": form.get("aspect_locked") == "1",
        "strip_metadata": form.get("strip_metadata") == "1",
        "preserve_transparency": form.get("preserve_transparency") == "1",
        "use_scale": form.get("use_scale") == "1",
        "scale_pct": _int_or_none(form.get("scale_pct")),
        "file_size_goal": form.get("file_size_goal") == "1",
        "file_size_value": _int_or_none(form.get("file_size_value")),
        "file_size_unit": form.get("file_size_unit"),
        "sample_rate": form.get("sample_rate"),
        "start_time": form.get("start_time") or None,
        "end_time": form.get("end_time") or None,
    }
    save_overrides(overrides)
    return HTMLResponse("")


@app.delete("/files/{file_id}/settings")
async def clear_file_settings(file_id: str):
    overrides = load_overrides()
    overrides.pop(file_id, None)
    save_overrides(overrides)
    return HTMLResponse("")


@app.delete("/files")
async def clear_files():
    save_files([])
    if UPLOADS_DIR.exists():
        shutil.rmtree(str(UPLOADS_DIR))
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    if CONV_OUTPUT_DIR.exists():
        shutil.rmtree(str(CONV_OUTPUT_DIR))
        CONV_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    save_overrides({})
    clear_conv_state()
    return HTMLResponse('<div id="file-list"></div>')


# ── Conversion ─────────────────────────────────────────────

def _resolve_settings(entry: dict) -> dict:
    cat = entry["category"]
    settings = load_settings()
    base = dict(settings.get(cat, {}))
    overrides = load_overrides()
    ov = overrides.get(entry["id"], {})
    if ov.get("active"):
        for key in ("output_format", "quality", "crf", "bitrate", "max_width", "max_height", "aspect_locked", "strip_metadata", "preserve_transparency", "use_scale", "scale_pct", "file_size_goal", "file_size_value", "file_size_unit", "sample_rate", "start_time", "end_time"):
            val = ov.get(key)
            if val is not None:
                base[key] = val
    return base


@app.post("/convert/{file_id}")
async def convert_single(file_id: str):
    entries = load_files()
    entry = next((e for e in entries if e["id"] == file_id), None)
    if not entry:
        return JSONResponse({"error": "File not found"}, status_code=404)

    if entry["category"] != "image":
        return JSONResponse({"error": "Only image conversion is available in alpha"}, status_code=400)

    state = get_status(file_id)
    if state and state["status"] in ("running", "pending"):
        return JSONResponse({"error": "Already converting"}, status_code=409)

    settings = _resolve_settings(entry)
    asyncio.create_task(start_conversion(file_id, entry, settings))
    return JSONResponse({"status": "started"})


@app.post("/convert")
async def convert_all():
    entries = load_files()
    count = 0
    for entry in entries:
        if entry["category"] != "image":
            continue
        fid = entry["id"]
        state = get_status(fid)
        if state and state["status"] in ("running", "pending"):
            continue
        settings = _resolve_settings(entry)
        asyncio.create_task(start_conversion(fid, entry, settings))
        count += 1
    return JSONResponse({"status": "started", "count": count})


@app.get("/convert/{file_id}/status")
async def convert_status(file_id: str):
    state = get_status(file_id)
    if not state:
        return JSONResponse({"status": "not_found", "progress": 0, "output_path": None, "error": None})
    return JSONResponse(state)


@app.get("/convert/status")
async def convert_all_statuses():
    return JSONResponse(get_all_statuses())


# ── Download ────────────────────────────────────────────────

@app.get("/download/{file_id}")
async def download_file(file_id: str):
    entries = load_files()
    entry = next((e for e in entries if e["id"] == file_id), None)
    if not entry:
        return JSONResponse({"error": "File not found"}, status_code=404)

    state = get_status(file_id)
    if not state or state["status"] != "done" or not state["output_path"]:
        return JSONResponse({"error": "Conversion not complete"}, status_code=400)

    output_path = Path(state["output_path"])
    if not output_path.exists():
        return JSONResponse({"error": "Output file not found"}, status_code=404)

    orig_stem = Path(entry["filename"]).stem
    download_name = f"{orig_stem}{output_path.suffix}"

    return FileResponse(path=str(output_path), filename=download_name, media_type="application/octet-stream")


@app.get("/download")
async def download_all_zip():
    entries = load_files()
    all_states = get_all_statuses()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in entries:
            fid = entry["id"]
            state = all_states.get(fid)
            if state and state["status"] == "done" and state["output_path"]:
                p = Path(state["output_path"])
                if p.exists():
                    orig_stem = Path(entry["filename"]).stem
                    zf.write(str(p), f"{orig_stem}{p.suffix}")

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=converted.zip"},
    )


# ── Audio Metadata Helper ──────────────────────────────────

async def _get_audio_info(filepath: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=bit_rate,duration",
        "-show_entries", "stream=sample_rate",
        "-of", "default=noprint_wrappers=1",
        str(filepath),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    info = {}
    for line in out.decode().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()
    return info


def _secs_to_timestamp(secs: float) -> str:
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


SAMPLE_RATES = ["8000", "11025", "16000", "22050", "44100", "48000", "96000", "192000"]

# ── Settings Form Builder ──────────────────────────────────

def _settings_form_html(category: str, values: dict, *, is_group: bool, file_id: str = "", filename: str = "", orig_w: int | None = None, orig_h: int | None = None, file_duration: float | None = None):
    fmt = values.get("output_format", "")
    quality = values.get("quality", 100)
    crf = values.get("crf")
    bitrate = values.get("bitrate", "")
    mw = values.get("max_width")
    mh = values.get("max_height")
    aspect_locked = values.get("aspect_locked", True)
    sample_rate = values.get("sample_rate", "")
    start_time = values.get("start_time", "")
    end_time = values.get("end_time", "")

    formats = _format_options(category)
    format_options = "".join(
        f'<option value="{v}"{" selected" if v == fmt else ""}>{l}</option>'
        for v, l in formats
    )

    title = filename if not is_group else {"image": "Image Settings", "video": "Video Settings", "audio": "Audio Settings"}.get(category, "Settings")

    has_resolution = category in ("image", "video")
    has_crf = category == "video"
    has_bitrate = category in ("video", "audio")

    # Quality slider for audio: disabled when lossless format selected
    rows = ""

    # Format
    rows += f"""
    <div class="settings-field">
      <label class="settings-label">Output Format</label>
      <select class="settings-select" name="output_format">{format_options}</select>
    </div>"""

    # Quality
    rows += f"""
    <div class="settings-field">
      <label class="settings-label">Quality: <span class="settings-q-val">{quality}</span></label>
      <input type="range" class="settings-slider" name="quality" min="1" max="100" value="{quality}">
      <div class="settings-range-labels">
        <span>Small file</span>
        <span>Best quality</span>
      </div>
    </div>"""

    # CRF (video only)
    if has_crf:
        crf_val = crf if crf is not None else 23
        rows += f"""
    <div class="settings-field">
      <label class="settings-label">CRF <span class="settings-hint">(0-51, lower = better)</span></label>
      <input type="number" class="settings-input" name="crf" value="{crf_val}" min="0" max="51" step="1">
    </div>"""

    # Bitrate
    if has_bitrate:
        br_placeholder = "e.g. 10M" if category == "video" else "e.g. 192k"
        rows += f"""
    <div class="settings-field">
      <label class="settings-label">Bitrate</label>
      <input type="text" class="settings-input" name="bitrate" value="{bitrate}" placeholder="{br_placeholder}">
    </div>"""

    # Sample rate (audio only, per-file)
    if category == "audio" and not is_group:
        sr_opts = "".join(
            f'<option value="{r}"{" selected" if r == sample_rate else ""}>{r} Hz</option>'
            for r in SAMPLE_RATES
        )
        rows += f"""
    <div class="settings-field">
      <label class="settings-label">Sample Rate</label>
      <select class="settings-select" name="sample_rate">{sr_opts}</select>
    </div>"""

    # Trim (per-file only)
    if not is_group and category in ("audio", "video"):
        end_default = _secs_to_timestamp(file_duration) if file_duration else ""
        end_val = end_time if end_time else end_default
        rows += f"""
    <div class="settings-field">
      <label class="settings-label">Trim</label>
      <div class="settings-resolution">
        <input type="text" class="settings-input settings-res-input" name="start_time" value="{start_time}" placeholder="0:00">
        <span class="settings-res-x">to</span>
        <input type="text" class="settings-input settings-res-input" name="end_time" value="{end_val}" placeholder="MM:SS">
      </div>
      <span class="settings-hint">Format: MM:SS or HH:MM:SS</span>
    </div>"""

    # Resolution / Scale (image/video only)
    if has_resolution:
        use_scale_mode = values.get("use_scale", False)
        scale_pct_val = values.get("scale_pct", "")

        if mw is not None:
            mw_str = f' value="{mw}"'
        elif not is_group and orig_w:
            mw_str = f' value="{orig_w}"'
        else:
            mw_str = ""

        if mh is not None:
            mh_str = f' value="{mh}"'
        elif not is_group and orig_h:
            mh_str = f' value="{orig_h}"'
        else:
            mh_str = ""

        aspect_attrs = ""
        if not is_group and orig_w and orig_h:
            aspect_attrs = f' data-orig-w="{orig_w}" data-orig-h="{orig_h}"'

        lock_icon = "&#x1F517;" if aspect_locked else "&#x1F512;"
        lock_title = "Lock aspect ratio" if aspect_locked else "Unlock aspect ratio"
        lock_val = "1" if aspect_locked else "0"
        use_scale_val = "1" if use_scale_mode else "0"
        sz_display = "none" if use_scale_mode else "flex"
        sc_display = "block" if use_scale_mode else "none"
        sz_tab = "" if use_scale_mode else " active"
        sc_tab = " active" if use_scale_mode else ""
        scale_pct_str = str(scale_pct_val) if scale_pct_val is not None else ""

        rows += f"""
    <div class="settings-field"{aspect_attrs}>
      <label class="settings-label">Resize</label>
      <div class="mode-tabs">
        <button type="button" class="mode-tab{sz_tab}" data-mode="size">Size</button>
        <button type="button" class="mode-tab{sc_tab}" data-mode="scale">Scale</button>
      </div>
      <input type="hidden" name="use_scale" value="{use_scale_val}">
      <div class="size-mode" style="display:{sz_display}">
        <div class="settings-resolution">
          <input type="number" class="settings-input settings-res-input settings-res-w" name="max_width" placeholder="W"{mw_str}>
          <button type="button" class="aspect-lock-btn" title="{lock_title}">{lock_icon}</button>
          <span class="settings-res-x">&#x00D7;</span>
          <input type="number" class="settings-input settings-res-input settings-res-h" name="max_height" placeholder="H"{mh_str}>
        </div>
        <input type="hidden" name="aspect_locked" value="{lock_val}">
      </div>
      <div class="scale-mode" style="display:{sc_display}">
        <input type="number" class="settings-input" name="scale_pct" value="{scale_pct_str}" placeholder="50" min="1" max="1000">
        <span class="settings-hint">Percentage of original size</span>
      </div>
    </div>"""

    # Strip Metadata
    strip_checked = ' checked' if values.get("strip_metadata") else ''
    rows += f"""
    <div class="settings-field settings-field-checkbox">
      <label class="settings-label-checkbox">
        <input type="checkbox" name="strip_metadata" value="1"{strip_checked}>
        Strip Metadata
      </label>
      <span class="settings-hint">Remove EXIF, tags, and metadata from output</span>
    </div>"""

    # Preserve Transparency (images only)
    if category == "image":
        trans_checked = ' checked' if values.get("preserve_transparency", True) else ''
        rows += f"""
    <div class="settings-field settings-field-checkbox">
      <label class="settings-label-checkbox">
        <input type="checkbox" name="preserve_transparency" value="1"{trans_checked}>
        Preserve Transparency
      </label>
      <span class="settings-hint">Keep alpha channel (PNG, WebP, TIFF, GIF, etc.). Uncheck to flatten against white. JPEG does not support transparency.</span>
    </div>"""

    # File Size Goal (images only)
    if category == "image":
        fsg_checked = ' checked' if values.get("file_size_goal") else ''
        fsg_val = values.get("file_size_value") or ""
        fsg_unit = values.get("file_size_unit") or "MB"
        fsg_display = "block" if values.get("file_size_goal") else "none"
        unit_opts = "".join(
            f'<option value="{u}"{" selected" if u == fsg_unit else ""}>{u}</option>'
            for u in ("KB", "KiB", "MB", "MiB", "GB", "GiB")
        )
        rows += f"""
    <div class="settings-field settings-field-checkbox">
      <label class="settings-label-checkbox">
        <input type="checkbox" name="file_size_goal" value="1"{fsg_checked}>
        File Size Goal
      </label>
    </div>
    <div class="settings-field" style="display:{fsg_display}">
      <div class="settings-inline">
        <input type="number" class="settings-input settings-size-input" name="file_size_value" value="{fsg_val}" placeholder="10" min="1">
        <select class="settings-select settings-unit-select" name="file_size_unit">{unit_opts}</select>
      </div>
      <span class="settings-hint">Target output size (JPEG only). Other formats ignore this.</span>
    </div>"""

    data_attrs = f'data-is-group="1"' if is_group else f'data-is-group="0" data-file-id="{file_id}"'

    tab_row = _tab_row(category) if is_group else ""

    return f"""<div class="settings-form" {data_attrs}>
  <div class="settings-header">{title}</div>
  {tab_row}
  <div class="settings-body">{rows}
  </div>
</div>"""


def _tab_row(active_cat: str) -> str:
    cats = [("image", "Images")]
    tabs = "".join(
        f'<button class="settings-tab{" active" if c == active_cat else ""}" data-cat="{c}">{l}</button>'
        for c, l in cats
    )
    return f'<div class="settings-tabs">{tabs}</div>'


def _format_options(category: str) -> list[tuple[str, str]]:
    if category == "image":
        return [(f, f) for f in IMAGE_FORMATS]
    if category == "video":
        return VIDEO_FORMATS
    if category == "audio":
        return [(f, f) for f in AUDIO_FORMATS]
    return []


def _int_or_none(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


# ── File Entry HTML ────────────────────────────────────────

def _file_entry_html(f: dict, has_override: bool = False) -> str:
    icon = _category_icon(f["category"])
    size_kb = f["size"] / 1024
    if size_kb < 1024:
        size_str = f"{size_kb:.1f} KB"
    else:
        size_str = f"{size_kb/1024:.1f} MB"
    cat = f["category"]
    name = f["filename"].replace('"', '&quot;')
    gear_class = "gear-btn" + (" edited" if has_override else "")
    return f"""<div class="file-entry" data-id="{f["id"]}" data-category="{cat}" data-name="{name}">
  <span class="file-icon">{icon}</span>
  <span class="file-name">{f["filename"]}</span>
  <span class="file-size">{size_str}</span>
  <button class="info-btn" data-id="{f["id"]}" title="Show metadata">&#x24D8;</button>
  <button class="{gear_class}" data-id="{f["id"]}" title="Conversion settings">&#x2699;</button>
  <button class="del-btn" data-id="{f["id"]}" title="Left‑click to confirm · Right‑click to remove">&#x1F5D1;</button>
  <button class="convert-btn" data-id="{f["id"]}" title="Convert this file">&#x25B6;</button>
</div>"""


def _category_icon(cat: str) -> str:
    return {"image": "&#x1F5BC;", "video": "&#x1F3AC;", "audio": "&#x1F3B5;"}.get(cat, "&#x1F4C4;")
