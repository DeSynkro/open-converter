import asyncio
import uuid
from pathlib import Path

from ..storage import UPLOADS_DIR
from . import OUTPUT_DIR, update_progress, set_done, set_error

FORMAT_EXT = {
    "MP4": ".mp4", "MKV": ".mkv", "WebM": ".webm",
    "MOV": ".mov", "AVI": ".avi", "FLV": ".flv",
}

# Output container -> (video codec, audio codec)
CONTAINER_CODECS = {
    "MP4": ("libx264", "aac"),
    "MKV": ("libx264", "aac"),
    "WebM": ("libvpx-vp9", "libopus"),
    "MOV": ("libx264", "aac"),
    "AVI": ("mpeg4", "libmp3lame"),
    "FLV": ("libx264", "aac"),
}

AUDIO_BITRATE = {
    "aac": "192k",
    "libopus": "128k",
    "libmp3lame": "192k",
}

# Rough mapping from the main preset names to VP9 -cpu-used values.
VP9_CPU_USED = {
    "ultrafast": 5,
    "superfast": 5,
    "veryfast": 4,
    "faster": 4,
    "fast": 3,
    "medium": 2,
    "slow": 1,
    "slower": 0,
    "veryslow": 0,
}


async def _progress_pulse(file_id: str):
    pct = 5
    while pct < 85:
        update_progress(file_id, pct)
        pct += 3
        await asyncio.sleep(1.0)


async def _probe_duration(path: Path):
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        return float(out.decode().strip())
    except (ValueError, OSError):
        return None


def _parse_time(value):
    if not value:
        return None
    try:
        parts = [float(p) for p in str(value).split(":")]
    except ValueError:
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def _timestamp_to_us(value):
    try:
        secs = 0.0
        for p in value.split(":"):
            secs = secs * 60 + float(p)
        return int(secs * 1_000_000)
    except (ValueError, AttributeError):
        return None


async def _read_progress(stream, file_id: str, duration):
    """Parse ffmpeg -progress output and map it onto 5-95 percent."""
    while True:
        line = await stream.readline()
        if not line:
            break
        if not duration:
            continue
        text = line.decode("utf-8", errors="replace").strip()
        if not text or "=" not in text:
            continue
        key, _, value = text.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "out_time_us":
            try:
                us = int(value)
            except ValueError:
                continue
        elif key == "out_time":
            us = _timestamp_to_us(value)
            if us is None:
                continue
        else:
            continue
        if us < 0:
            continue
        frac = min(1.0, (us / 1_000_000.0) / duration)
        update_progress(file_id, 5 + int(frac * 90))


def _user_error(stderr: str) -> str:
    lower = stderr.lower()
    if (
        "not currently supported in container" in lower
        or "not supported in container" in lower
        or "supported for webm" in lower
        or "only vp8 or vp9" in lower
    ):
        return "The original audio format is not supported by the chosen output format. Uncheck 'Keep original audio format' and try again."
    if "could not write header" in lower:
        return "The output format could not be written with these settings. If 'Keep original audio format' is enabled, uncheck it and try again."
    if "unknown encoder" in lower or "encoder not found" in lower:
        return "The selected output format is not available on this system."
    if "invalid data found" in lower:
        return "This file could not be read. It may be corrupted or use an unsupported codec."
    if "no such file" in lower:
        return "The input file could not be found."
    return f"Conversion failed:\n{stderr[:300]}"


def _scale_filter(settings: dict):
    if settings.get("use_scale") and settings.get("scale_pct"):
        try:
            pct = float(settings["scale_pct"])
        except (TypeError, ValueError):
            pct = 0
        if pct > 0:
            return f"scale=w='trunc(iw*{pct}/100/2)*2':h=-2"
    max_width = settings.get("max_width")
    max_height = settings.get("max_height")
    if max_width and max_height:
        if not settings.get("aspect_locked", True):
            # Unlocked: use exact dimensions, which can stretch the video.
            return f"scale=w='trunc({max_width}/2)*2':h='trunc({max_height}/2)*2'"
        return (
            f"scale=w='min(iw,{max_width})':h='min(ih,{max_height})'"
            ":force_original_aspect_ratio=decrease:force_divisible_by=2"
        )
    if max_width:
        return f"scale=w='min(iw,{max_width})':h=-2"
    if max_height:
        return f"scale=w=-2:h='min(ih,{max_height})'"
    return None


def _build_filters(settings: dict):
    filters = []
    crop_w = settings.get("crop_w")
    crop_h = settings.get("crop_h")
    if crop_w and crop_h:
        try:
            cw = int(crop_w)
            ch = int(crop_h)
            cx = max(0, int(settings.get("crop_x") or 0))
            cy = max(0, int(settings.get("crop_y") or 0))
        except (TypeError, ValueError):
            cw = ch = 0
        if cw >= 2 and ch >= 2:
            # Force even dimensions so yuv420p encoding stays valid.
            filters.append(
                f"crop=w='trunc({cw}/2)*2':h='trunc({ch}/2)*2':x={cx}:y={cy}"
            )
    scale = _scale_filter(settings)
    if scale:
        filters.append(scale)
    return ",".join(filters) if filters else None


def _crf_to_qscale(crf: int) -> int:
    # MPEG-4 Part 2 has no CRF; map 0-51 onto a sane qscale range (2-31).
    return max(2, min(31, round(2 + (crf / 51) * 29)))


def _build_command(input_path: Path, output_path: Path, settings: dict) -> list[str]:
    container = settings.get("output_format", "MP4")
    if container not in CONTAINER_CODECS:
        container = "MP4"
    vcodec, acodec = CONTAINER_CODECS[container]

    crf = settings.get("crf", 23)
    if crf is None:
        crf = 23
    crf = max(0, min(51, int(crf)))
    preset = settings.get("preset") or "medium"

    cmd = ["ffmpeg", "-i", str(input_path), "-y"]

    if settings.get("strip_metadata"):
        cmd.extend(["-map_metadata", "-1"])

    # Video
    if vcodec == "mpeg4":
        cmd.extend(["-c:v", "mpeg4", "-q:v", str(_crf_to_qscale(crf))])
    elif vcodec == "libvpx-vp9":
        cpu = VP9_CPU_USED.get(preset, 2)
        cmd.extend([
            "-c:v", "libvpx-vp9",
            "-crf", str(crf), "-b:v", "0",
            "-deadline", "good", "-cpu-used", str(cpu), "-row-mt", "1",
        ])
    else:
        cmd.extend(["-c:v", vcodec, "-crf", str(crf), "-preset", preset])

    cmd.extend(["-pix_fmt", "yuv420p"])

    # Audio
    if settings.get("copy_audio"):
        cmd.extend(["-c:a", "copy"])
    else:
        cmd.extend(["-c:a", acodec, "-b:a", AUDIO_BITRATE.get(acodec, "192k")])

    # Resolution
    vf = _build_filters(settings)
    if vf:
        cmd.extend(["-vf", vf])

    # Trim
    start_time = settings.get("start_time")
    if start_time:
        cmd.extend(["-ss", start_time])
    end_time = settings.get("end_time")
    if end_time:
        cmd.extend(["-to", end_time])

    if container in ("MP4", "MOV"):
        cmd.extend(["-movflags", "+faststart"])

    cmd.extend(["-nostats", "-progress", "pipe:1"])

    cmd.append(str(output_path))
    return cmd


async def convert(file_id: str, entry: dict, settings: dict):
    try:
        ext = Path(entry["filename"]).suffix
        input_path = UPLOADS_DIR / f"{file_id}{ext}"

        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        update_progress(file_id, 3)

        container = settings.get("output_format", "MP4")
        output_ext = FORMAT_EXT.get(container, ".mp4")
        output_path = OUTPUT_DIR / f"{file_id}_{uuid.uuid4().hex[:12]}{output_ext}"

        duration = await _probe_duration(input_path)
        start = _parse_time(settings.get("start_time"))
        end = _parse_time(settings.get("end_time"))
        out_duration = duration
        if duration:
            if start:
                out_duration -= start
            if end:
                out_duration = end - (start or 0.0)
            if out_duration <= 0:
                out_duration = duration

        cmd = _build_command(input_path, output_path, settings)

        update_progress(file_id, 5)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stderr_task = asyncio.create_task(proc.stderr.read())
        progress_task = asyncio.create_task(
            _read_progress(proc.stdout, file_id, out_duration)
        )
        pulse = None
        if not out_duration:
            pulse = asyncio.create_task(_progress_pulse(file_id))

        stderr_data = await stderr_task
        await progress_task
        if pulse:
            pulse.cancel()
        await proc.wait()

        if proc.returncode != 0:
            err_text = stderr_data.decode("utf-8", errors="replace").strip()
            raise RuntimeError(_user_error(err_text) or "ffmpeg failed")

        if not output_path.exists():
            raise RuntimeError("Output file was not created")

        update_progress(file_id, 95)
        set_done(file_id, str(output_path))

    except Exception as e:
        set_error(file_id, str(e))
