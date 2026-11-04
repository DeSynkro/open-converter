import asyncio
import io
import json
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
    reconstruct_state,
)
from .metadata import get_dimensions, get_dpi, get_extended_metadata, get_metadata
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
    THUMBS_DIR,
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
    reconstruct_state()
    conv_state = get_all_statuses()
    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "files": files,
        "overrides": overrides,
        "conv_state": conv_state,
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
        html_parts.append(_file_entry_html(d, has_ov, get_all_statuses()))

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
    (THUMBS_DIR / f"{file_id}.jpg").unlink(missing_ok=True)

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
        "quality": int(form.get("quality", 100)),
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
        "sample_rate": form.get("sample_rate") or None,
        "channels": form.get("channels") or None,
        "manual_bitrate": form.get("manual_bitrate") == "1",
        "preset": form.get("preset") or None,
        "copy_audio": form.get("copy_audio") == "1",
    }
    save_settings(settings)
    return HTMLResponse("")


@app.get("/files/{file_id}/thumb")
async def file_thumb(file_id: str):
    entries = load_files()
    entry = next((e for e in entries if e["id"] == file_id), None)
    if not entry:
        return JSONResponse({"error": "File not found"}, status_code=404)

    ext = Path(entry["filename"]).suffix
    filepath = UPLOADS_DIR / f"{entry['id']}{ext}"
    if not filepath.exists():
        return JSONResponse({"error": "File not found on disk"}, status_code=404)

    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    thumb_path = THUMBS_DIR / f"{file_id}.jpg"

    if not thumb_path.exists() or thumb_path.stat().st_size == 0:
        info = await _get_video_info(filepath)
        dur = info.get("duration") or 0
        ts = min(1.0, dur / 2) if dur else 0
        attempts = [ts] if ts <= 0 else [ts, 0]
        for at in attempts:
            cmd = [
                "ffmpeg", "-ss", f"{at:.3f}", "-i", str(filepath),
                "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2",
                "-q:v", "3", "-y", str(thumb_path),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if thumb_path.exists() and thumb_path.stat().st_size > 0:
                break
        if not thumb_path.exists() or thumb_path.stat().st_size == 0:
            return JSONResponse({"error": "Could not generate thumbnail"}, status_code=500)

    return FileResponse(str(thumb_path), media_type="image/jpeg")


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
        for key in ("output_format", "quality", "crf", "bitrate", "max_width", "max_height", "aspect_locked", "strip_metadata", "preserve_transparency", "use_scale", "scale_pct", "file_size_goal", "file_size_value", "file_size_unit", "dpi", "sample_rate", "channels", "manual_bitrate", "start_time", "end_time", "preset", "copy_audio", "crop_x", "crop_y", "crop_w", "crop_h", "crop_locked"):
            val = override.get(key)
            if val is not None:
                merged[key] = val

    ow, oh, orig_dpi, orig_sample_rate, orig_channels, orig_duration = None, None, None, None, None, None
    source_info = ""
    ext = Path(entry["filename"]).suffix
    filepath = UPLOADS_DIR / f"{entry['id']}{ext}"
    if filepath.exists():
        if cat == "image":
            ow, oh = get_dimensions(filepath, entry["mime_type"])
            orig_dpi = get_dpi(filepath, entry["mime_type"])
        elif cat == "video":
            info = await _get_video_info(filepath)
            ow = info.get("width")
            oh = info.get("height")
            dur = info.get("duration")
            if dur:
                orig_duration = float(dur)
            parts = []
            if info.get("codec"):
                parts.append(str(info["codec"]).upper())
            if ow and oh:
                parts.append(f"{ow} x {oh}")
            fps = info.get("fps")
            if fps and "/" in fps:
                try:
                    num, den = fps.split("/")
                    parts.append(f"{float(num) / float(den):.2f} fps")
                except (ValueError, ZeroDivisionError):
                    pass
            if info.get("bit_rate"):
                parts.append(f"{int(info['bit_rate']) // 1000} kbps")
            if parts:
                source_info = "Source: " + " | ".join(parts)
        elif cat == "audio":
            info = await _get_audio_info(filepath)
            sr = info.get("sample_rate")
            if sr:
                orig_sample_rate = sr
            ch = info.get("channels")
            if ch:
                orig_channels = ch
            dur = info.get("duration")
            if dur:
                orig_duration = float(dur)
            if "bit_rate" in info and not merged.get("manual_bitrate"):
                merged["bitrate"] = str(int(info["bit_rate"]) // 1000)

    return HTMLResponse(_settings_form_html(cat, merged, is_group=False, file_id=file_id, filename=entry["filename"], orig_w=ow, orig_h=oh, orig_dpi=orig_dpi, orig_sample_rate=orig_sample_rate, orig_channels=orig_channels, orig_duration=orig_duration, source_info=source_info))


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
        "dpi": _int_or_none(form.get("dpi")),
        "sample_rate": form.get("sample_rate") or None,
        "channels": form.get("channels") or None,
        "manual_bitrate": form.get("manual_bitrate") == "1",
        "start_time": form.get("start_time") or None,
        "end_time": form.get("end_time") or None,
        "preset": form.get("preset") or None,
        "copy_audio": form.get("copy_audio") == "1",
        "crop_x": _int_or_none(form.get("crop_x")),
        "crop_y": _int_or_none(form.get("crop_y")),
        "crop_w": _int_or_none(form.get("crop_w")),
        "crop_h": _int_or_none(form.get("crop_h")),
        "crop_locked": form.get("crop_locked") == "1",
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
    if THUMBS_DIR.exists():
        shutil.rmtree(str(THUMBS_DIR))
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
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
        for key in ("output_format", "quality", "crf", "bitrate", "max_width", "max_height", "aspect_locked", "strip_metadata", "preserve_transparency", "use_scale", "scale_pct", "file_size_goal", "file_size_value", "file_size_unit", "dpi", "sample_rate", "channels", "manual_bitrate", "start_time", "end_time", "preset", "copy_audio", "crop_x", "crop_y", "crop_w", "crop_h", "crop_locked"):
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

    if entry["category"] not in ("image", "audio", "video"):
        return JSONResponse({"error": "Only image, audio, and video conversion are available"}, status_code=400)

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
        if entry["category"] not in ("image", "audio", "video"):
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
    seen_names = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in entries:
            fid = entry["id"]
            state = all_states.get(fid)
            if state and state["status"] == "done" and state["output_path"]:
                p = Path(state["output_path"])
                if p.exists():
                    orig_stem = Path(entry["filename"]).stem
                    arcname = f"{orig_stem}{p.suffix}"
                    if arcname in seen_names:
                        seen_names[arcname] += 1
                        arcname = f"{orig_stem}_{seen_names[arcname]}{p.suffix}"
                    else:
                        seen_names[arcname] = 1
                    zf.write(str(p), arcname)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=converted.zip"},
    )


# ── Reorder ─────────────────────────────────────────────────

@app.put("/files/reorder")
async def reorder_files(request: Request):
    form = await request.form()
    order = form.getlist("order")
    entries = load_files()
    ordered = sorted(entries, key=lambda e: order.index(e["id"]))
    save_files(ordered)
    return HTMLResponse("")


# ── Storage Tracker ────────────────────────────────────────

@app.get("/storage")
async def storage_usage():
    def dir_size(p):
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    uploads = dir_size(UPLOADS_DIR) if UPLOADS_DIR.exists() else 0
    outputs = dir_size(CONV_OUTPUT_DIR) if CONV_OUTPUT_DIR.exists() else 0
    return JSONResponse({"uploads": uploads, "outputs": outputs})


# ── Audio Metadata Helper ──────────────────────────────────

async def _get_audio_info(filepath: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=bit_rate,duration",
        "-show_entries", "stream=sample_rate,channels",
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


async def _get_video_info(filepath: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(filepath),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    try:
        data = json.loads(out.decode() or "{}")
    except json.JSONDecodeError:
        return {}
    info = {}
    fmt = data.get("format", {})
    if fmt.get("duration"):
        info["duration"] = float(fmt["duration"])
    if fmt.get("bit_rate"):
        info["bit_rate"] = int(fmt["bit_rate"])
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and "codec" not in info:
            width = s.get("width")
            height = s.get("height")
            rot = 0
            for sd in s.get("side_data_list") or []:
                if "rotation" in sd:
                    try:
                        rot = int(float(sd["rotation"]))
                    except (TypeError, ValueError):
                        pass
                    break
            if not rot:
                tags = s.get("tags") or {}
                for key in ("rotate", "rotation"):
                    if key in tags:
                        try:
                            rot = int(float(tags[key]))
                        except (TypeError, ValueError):
                            pass
                        break
            if width and height and abs(rot) % 180 == 90:
                width, height = height, width
            info["width"] = width
            info["height"] = height
            info["codec"] = s.get("codec_name")
            info["fps"] = s.get("r_frame_rate")
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

def _settings_form_html(category: str, values: dict, *, is_group: bool, file_id: str = "", filename: str = "", orig_w: int | None = None, orig_h: int | None = None, orig_dpi: int | None = None, orig_sample_rate: str | None = None, orig_channels: str | None = None, orig_duration: float | None = None, source_info: str = ""):
    fmt = values.get("output_format", "")
    quality = values.get("quality", 100)
    crf = values.get("crf", 23)
    if crf is None:
        crf = 23
    mw = values.get("max_width")
    mh = values.get("max_height")
    aspect_locked = values.get("aspect_locked", True)
    dpi = values.get("dpi", "")
    manual_bitrate = values.get("manual_bitrate", False)
    bitrate = values.get("bitrate", "") or ""
    sample_rate = values.get("sample_rate", "") or ""
    channels = values.get("channels", "") or ""
    start_time = values.get("start_time", "") or ""
    end_time = values.get("end_time", "") or ""

    formats = _format_options(category)
    format_options = "".join(
        f'<option value="{v}"{" selected" if v == fmt else ""}>{l}</option>'
        for v, l in formats
    )

    title = filename if not is_group else {"image": "Image Settings", "audio": "Audio Settings", "video": "Video Settings"}.get(category, "Settings")

    is_audio = category == "audio"
    is_video = category == "video"
    has_resolution = category in ("image", "video")
    rows = ""
    audio_rows = ""

    if is_video and source_info:
        rows += f"""
    <div class="settings-field">
      <span class="settings-hint">{source_info}</span>
    </div>"""

    # Format
    rows += f"""
    <div class="settings-field">
      <label class="settings-label">Output Format</label>
      <select class="settings-select" name="output_format">{format_options}</select>
    </div>"""

    # Quality (images, audio) or CRF (video)
    if is_video:
        rows += f"""
    <div class="settings-field">
      <label class="settings-label">Quality (CRF): <span class="settings-q-val">{crf}</span></label>
      <input type="range" class="settings-slider settings-slider-reverse" name="crf" min="0" max="51" value="{crf}">
      <div class="settings-range-labels">
        <span>Smaller file</span>
        <span>Best quality</span>
      </div>
      <span class="settings-hint">Lower CRF means higher quality and larger files. 18 to 28 is a good range.</span>
    </div>"""
    else:
        rows += f"""
    <div class="settings-field">
      <label class="settings-label">Quality: <span class="settings-q-val">{quality}</span></label>
      <input type="range" class="settings-slider" name="quality" min="1" max="100" value="{quality}">
      <div class="settings-range-labels">
        <span>Small file</span>
        <span>Best quality</span>
      </div>
    </div>"""

    # Encoding preset (video only)
    if is_video:
        preset = values.get("preset") or "medium"
        presets = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
        preset_opts = "".join(
            f'<option value="{p}"{" selected" if p == preset else ""}>{p}</option>'
            for p in presets
        )
        rows += f"""
    <div class="settings-field">
      <label class="settings-label">Encoding Preset</label>
      <select class="settings-select" name="preset">{preset_opts}</select>
      <span class="settings-hint">Slower presets produce smaller files at the same quality.</span>
    </div>"""

    # Keep original audio (video only, audio sub-tab)
    if is_video:
        copy_audio_checked = ' checked' if values.get("copy_audio") else ''
        copy_audio_tip = "Copies the original audio without re-encoding, so it stays lossless and the video converts faster. If the output format does not support the source audio codec (for example MP3 in WebM), the conversion may fail or play without sound. Leave unchecked to convert the audio automatically."
        audio_rows += f"""
    <div class="settings-field settings-field-checkbox">
      <label class="settings-label-checkbox">
        <input type="checkbox" name="copy_audio" value="1"{copy_audio_checked}>
        Keep original audio format
      </label>
      <button type="button" class="settings-help" data-tip="{copy_audio_tip}" aria-label="About keeping the original audio format">&#x24D8;</button>
    </div>"""

    # Manual bitrate (audio only)
    if is_audio:
        mb_checked = ' checked' if manual_bitrate else ''
        mb_display = "block" if manual_bitrate else "none"
        rows += f"""
    <div class="settings-field settings-field-checkbox">
      <label class="settings-label-checkbox">
        <input type="checkbox" name="manual_bitrate" value="1"{mb_checked}>
        Manual bitrate
      </label>
    </div>
    <div class="settings-field" style="display:{mb_display}">
      <label class="settings-label">Bitrate</label>
      <div class="settings-inline">
        <input type="text" class="settings-input" name="bitrate" value="{bitrate}" placeholder="e.g. 192">
        <span class="settings-res-x">kbps</span>
      </div>
      <span class="settings-hint">Target bitrate is a guide, not a hard limit. Actual output varies by encoder, sample rate, and audio content.</span>
    </div>"""

    # Sample rate (audio only)
    if is_audio:
        sr_opts = ""
        if is_group:
            sr_opts += '<option value=""' + (' selected' if not sample_rate else '') + '>Default (keep original)</option>'
        elif not is_group and orig_sample_rate:
            sr_selected = " selected" if not sample_rate or sample_rate == orig_sample_rate else ""
            sr_opts += f'<option value="{orig_sample_rate}"{sr_selected}>{orig_sample_rate} Hz (original)</option>'
        for r in SAMPLE_RATES:
            sr_selected = ' selected' if r == sample_rate else ''
            sr_opts += f'<option value="{r}"{sr_selected}>{r} Hz</option>'
        rows += f"""
    <div class="settings-field">
      <label class="settings-label">Sample Rate</label>
      <select class="settings-select" name="sample_rate">{sr_opts}</select>
      <span class="settings-hint">High rates (96000/192000 Hz) may fail with lossy formats</span>
    </div>"""

    # Channels (audio only)
    if is_audio:
        ch_opts = ""
        if is_group:
            ch_opts += '<option value=""' + (' selected' if not channels else '') + '>Default (keep original)</option>'
        elif not is_group and orig_channels:
            ch_selected = " selected" if not channels or channels == orig_channels else ""
            ch_label = "Mono" if orig_channels == "1" else "Stereo" if orig_channels == "2" else f"{orig_channels} channels"
            ch_opts += f'<option value="{orig_channels}"{ch_selected}>{ch_label} (original)</option>'
        for val, label in [("1", "Mono"), ("2", "Stereo")]:
            ch_selected = ' selected' if val == channels else ''
            ch_opts += f'<option value="{val}"{ch_selected}>{label}</option>'
        rows += f"""
    <div class="settings-field">
      <label class="settings-label">Channels</label>
      <select class="settings-select" name="channels">{ch_opts}</select>
    </div>"""

    # Trim (audio and video, per-file)
    if (is_audio or is_video) and not is_group:
        trim_checked = ' checked' if start_time or end_time else ''
        trim_display = "block" if start_time or end_time else "none"
        end_default = _secs_to_timestamp(orig_duration) if orig_duration else ""
        end_val = end_time if end_time else end_default
        rows += f"""
    <div class="settings-field settings-field-checkbox">
      <label class="settings-label-checkbox">
        <input type="checkbox" class="trim-toggle" value="1"{trim_checked}>
        Trim
      </label>
    </div>
    <div class="settings-field" style="display:{trim_display}">
      <label class="settings-label">Start</label>
      <input type="text" class="settings-input" name="start_time" value="{start_time}" placeholder="0:00">
    </div>
    <div class="settings-field" style="display:{trim_display}">
      <label class="settings-label">End</label>
      <input type="text" class="settings-input" name="end_time" value="{end_val}" placeholder="{end_default}">
      <span class="settings-hint">Format: MM:SS or HH:MM:SS</span>
    </div>"""

    # Crop (video, per-file)
    if is_video and not is_group:
        crop_x = values.get("crop_x")
        crop_y = values.get("crop_y")
        crop_w = values.get("crop_w")
        crop_h = values.get("crop_h")
        crop_on = bool(crop_w and crop_h)
        crop_checked = ' checked' if crop_on else ''
        crop_display = "block" if crop_on else "none"
        cx_val = crop_x if crop_x is not None else 0
        cy_val = crop_y if crop_y is not None else 0
        cw_val = crop_w if crop_w is not None else (orig_w or "")
        ch_val = crop_h if crop_h is not None else (orig_h or "")
        crop_locked = values.get("crop_locked", True)
        crop_lock_icon = "&#x1F517;" if crop_locked else "&#x1F512;"
        crop_lock_title = "Lock aspect ratio" if crop_locked else "Unlock aspect ratio"
        crop_lock_val = "1" if crop_locked else "0"
        crop_attrs = f' data-orig-w="{orig_w}" data-orig-h="{orig_h}"' if orig_w and orig_h else ""
        preview_html = ""
        if file_id and orig_w and orig_h:
            preview_html = f"""
    <div class="settings-field crop-preview-field" style="display:{crop_display}">
      <div class="crop-preview" data-orig-w="{orig_w}" data-orig-h="{orig_h}">
        <img class="crop-preview-img" src="/files/{file_id}/thumb" alt="Video preview">
        <div class="crop-rect"></div>
      </div>
    </div>"""
        rows += f"""
    <div class="settings-field settings-field-checkbox">
      <label class="settings-label-checkbox">
        <input type="checkbox" class="crop-toggle" value="1"{crop_checked}>
        Crop
      </label>
    </div>{preview_html}
    <div class="settings-field crop-pos-field" style="display:{crop_display}">
      <label class="settings-label">Crop Position</label>
      <div class="settings-inline">
        <input type="number" class="settings-input settings-res-input" name="crop_x" value="{cx_val}" placeholder="X" min="0">
        <span class="settings-res-x">X</span>
        <input type="number" class="settings-input settings-res-input" name="crop_y" value="{cy_val}" placeholder="Y" min="0">
        <span class="settings-res-x">Y</span>
      </div>
    </div>
    <div class="settings-field crop-field crop-size-field"{crop_attrs} style="display:{crop_display}">
      <label class="settings-label">Crop Size</label>
      <div class="settings-inline">
        <input type="number" class="settings-input settings-res-input crop-w" name="crop_w" value="{cw_val}" placeholder="W" min="2">
        <button type="button" class="aspect-lock-btn" title="{crop_lock_title}">{crop_lock_icon}</button>
        <span class="settings-res-x">&#x00D7;</span>
        <input type="number" class="settings-input settings-res-input crop-h" name="crop_h" value="{ch_val}" placeholder="H" min="2">
        <span class="settings-res-x">px</span>
      </div>
      <input type="hidden" name="crop_locked" value="{crop_lock_val}">
      <span class="settings-hint">Cut out a region before resizing. X and Y are the top-left corner. Leave unchecked to keep the full frame.</span>
    </div>"""

    # Resolution / Scale (image only)
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
    <div class="settings-field resize-field"{aspect_attrs}>
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

    # DPI (image only)
    if category == "image":
        if dpi:
            dpi_val = f' value="{dpi}"'
        elif not is_group and orig_dpi:
            dpi_val = f' value="{orig_dpi}"'
        else:
            dpi_val = ""
        rows += f"""
    <div class="settings-field">
      <label class="settings-label">DPI</label>
      <input type="number" class="settings-input settings-res-input" name="dpi"{dpi_val} placeholder="DPI" min="72" max="2400">
      <span class="settings-hint">Dots per inch for output (JPEG, PNG, TIFF)</span>
    </div>"""

    # Strip Metadata
    strip_checked = ' checked' if values.get("strip_metadata") else ''
    rows += f"""
    <div class="settings-field settings-field-checkbox">
      <label class="settings-label-checkbox">
        <input type="checkbox" name="strip_metadata" value="1"{strip_checked}>
        Strip Metadata
      </label>
      <span class="settings-hint">Remove metadata from output</span>
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

    if is_video:
        body = f"""
  <div class="video-subtabs">
    <button type="button" class="mode-tab active" data-vtab="video">Video</button>
    <button type="button" class="mode-tab" data-vtab="audio">Audio</button>
  </div>
  <div class="video-panel video-panel-video">{rows}</div>
  <div class="video-panel video-panel-audio" style="display:none">{audio_rows}</div>"""
    else:
        body = rows

    return f"""<div class="settings-form" {data_attrs}>
  <div class="settings-header">{title}</div>
  {tab_row}
  <div class="settings-body">{body}
  </div>
</div>"""


def _tab_row(active_cat: str) -> str:
    cats = [("image", "Images"), ("audio", "Audio"), ("video", "Video")]
    tabs = "".join(
        f'<button class="settings-tab{" active" if c == active_cat else ""}" data-cat="{c}">{l}</button>'
        for c, l in cats
    )
    return f'<div class="settings-tabs">{tabs}</div>'


def _format_options(category: str) -> list[tuple[str, str]]:
    if category == "image":
        return [(f, f) for f in IMAGE_FORMATS]
    if category == "audio":
        return [(f, f) for f in AUDIO_FORMATS]
    if category == "video":
        return [(f, f) for f in VIDEO_FORMATS]
    return []


def _int_or_none(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


# ── File Entry HTML ────────────────────────────────────────

def _file_entry_html(f: dict, has_override: bool = False, conv_state: dict | None = None) -> str:
    icon = _category_icon(f["category"])
    size_kb = f["size"] / 1024
    if size_kb < 1024:
        size_str = f"{size_kb:.1f} KB"
    else:
        size_str = f"{size_kb/1024:.1f} MB"
    cat = f["category"]
    name = f["filename"].replace('"', '&quot;')
    gear_class = "gear-btn" + (" edited" if has_override else "")
    state = (conv_state or {}).get(f["id"], {})
    st = state.get("status")
    if st == "done":
        conv_class = "convert-btn done"
        conv_icon = "&#x2B07;"
    elif st in ("running", "pending"):
        conv_class = "convert-btn converting"
        conv_icon = ""
    else:
        conv_class = "convert-btn"
        conv_icon = "&#x25B6;"
    return f"""<div class="file-entry" draggable="true" data-id="{f["id"]}" data-category="{cat}" data-name="{name}">
  <span class="file-icon">{icon}</span>
  <span class="file-name">{f["filename"]}</span>
  <span class="file-size">{size_str}</span>
  <button class="info-btn" data-id="{f["id"]}" title="Show metadata">&#x24D8;</button>
  <button class="{gear_class}" data-id="{f["id"]}" title="Conversion settings">&#x2699;</button>
  <button class="del-btn" data-id="{f["id"]}" title="Left‑click to confirm · Right‑click to remove">&#x1F5D1;</button>
  <button class="{conv_class}" data-id="{f["id"]}">{conv_icon}</button>
  <div class="file-progress"><div class="file-progress-fill"></div></div>
</div>"""


def _category_icon(cat: str) -> str:
    return {"image": "&#x1F5BC;", "video": "&#x1F3AC;", "audio": "&#x1F3B5;"}.get(cat, "&#x1F4C4;")
