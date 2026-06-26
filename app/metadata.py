import json
import subprocess
from pathlib import Path

from PIL import Image, ExifTags


def get_metadata(filepath: Path, mime_type: str) -> dict:
    if mime_type.startswith("image/"):
        return _image_metadata(filepath)
    if mime_type.startswith("video/"):
        return _video_metadata(filepath)
    if mime_type.startswith("audio/"):
        return _audio_metadata(filepath)
    return {}


def get_extended_metadata(filepath: Path, mime_type: str) -> dict:
    if mime_type.startswith("image/"):
        return _extended_image_metadata(filepath)
    if mime_type.startswith("video/"):
        return _extended_video_metadata(filepath)
    if mime_type.startswith("audio/"):
        return _extended_audio_metadata(filepath)
    return {}


# ── Basic Image ─────────────────────────────────────────────

def _image_metadata(path: Path) -> dict:
    try:
        with Image.open(path) as img:
            info = {
                "Format": img.format or "Unknown",
                "Dimensions": f"{img.width} &#x00D7; {img.height}",
                "Color Mode": img.mode,
            }
            if "dpi" in img.info:
                dpi = img.info["dpi"]
                info["DPI"] = f"{int(dpi[0])} &#x00D7; {int(dpi[1])}"
            if hasattr(img, "is_animated") and img.is_animated:
                info["Frames"] = str(getattr(img, "n_frames", "?"))
            return info
    except Exception:
        return {}


def _extended_image_metadata(path: Path) -> dict:
    info = _image_metadata(path)
    exif = _exif_data(path)
    # Only add EXIF fields that aren't already shown
    for k, v in exif.items():
        display = _humanize_exif(k, v)
        if display is None:
            continue
        info[display[0]] = display[1]
    return info


# ── EXIF ────────────────────────────────────────────────────

_EXIF_SKIP_KEYS = {
    "MakerNote", "UserComment", "ComponentsConfiguration",
    "SceneType", "FileSource", "FlashpixVersion",
    "InteroperabilityIFDPointer", "ExifIFDPointer", "GPSInfo",
}

_EXIF_LABEL_OVERRIDES = {
    "DateTimeOriginal": "Date Taken",
    "DateTimeDigitized": "Digitized",
    "ExposureTime": "Shutter Speed",
    "FNumber": "Aperture",
    "ISOSpeedRatings": "ISO",
    "FocalLength": "Focal Length",
    "FocalLengthIn35mmFilm": "Focal Length (35mm)",
    "MaxApertureValue": "Max Aperture",
    "WhiteBalance": "White Balance",
    "DigitalZoomRatio": "Digital Zoom",
    "SceneCaptureType": "Scene Mode",
    "SubjectDistanceRange": "Subject Distance",
}

_FLASH_MAP = {
    0x00: "No Flash", 0x01: "Fired", 0x05: "Fired (No Strobe Return)",
    0x07: "Fired (Strobe Return)", 0x09: "Fired (Compulsory)",
    0x0D: "Fired (Compulsory Return)", 0x0F: "Fired (Compulsory Return)",
    0x10: "No Flash (Compulsory)", 0x14: "No Flash (Auto)",
    0x18: "No Flash (Auto)", 0x19: "Fired (Auto)", 0x1D: "Fired (Auto Return)",
    0x20: "No Flash", 0x41: "Fired (Red-Eye)",
    0x45: "Fired (Red-Eye No Return)", 0x47: "Fired (Red-Eye Return)",
    0x49: "Fired (Compulsory Red-Eye)", 0x4D: "Fired (Compulsory Red-Eye Return)",
    0x4F: "Fired (Compulsory Red-Eye Return)", 0x50: "No Flash (Auto)",
    0x58: "No Flash (Auto)", 0x59: "Fired (Auto Red-Eye)",
    0x5D: "Fired (Auto Red-Eye Return)",
}


def _exif_data(path: Path) -> dict:
    try:
        with Image.open(path) as img:
            raw = img._getexif()
            if not raw:
                return {}
            result = {}
            for tag_id, value in raw.items():
                name = ExifTags.TAGS.get(tag_id, str(tag_id))
                if name in _EXIF_SKIP_KEYS:
                    continue
                if isinstance(value, bytes):
                    continue
                if name == "GPSInfo":
                    continue
                result[name] = value
            return result
    except Exception:
        return {}


def _humanize_exif(key: str, value):
    label = _EXIF_LABEL_OVERRIDES.get(key, key)

    if label == "Shutter Speed" and isinstance(value, tuple):
        num, den = value
        if den:
            return (label, f"{num}/{den}s" if num == 1 else f"{num/den:.3f}s")
        return (label, str(value))

    if label == "Aperture" and isinstance(value, tuple):
        num, den = value
        if den:
            return (label, f"f/{num/den:.1f}")
        return (label, str(value))

    if label == "Focal Length" and isinstance(value, tuple):
        num, den = value
        if den:
            return (label, f"{num/den:.0f}mm")
        return (label, str(value))

    if label == "Focal Length (35mm)" and isinstance(value, tuple):
        num, den = value
        return (label, f"{num/den:.0f}mm" if den else str(value))

    if label == "Max Aperture" and isinstance(value, tuple):
        num, den = value
        return (label, f"f/{num/den:.1f}" if den else str(value))

    if label == "ISO" and isinstance(value, (int, float)):
        return (label, f"ISO {int(value)}")

    if label == "Flash":
        val = int(value) if not isinstance(value, int) else value
        return (label, _FLASH_MAP.get(val, str(val)))

    if label in ("Date Taken", "Digitized", "DateTime"):
        s = str(value).replace(":", "-", 2) if ":" in str(value) else str(value)
        return (label, s)

    if label == "ExposureBiasValue" and isinstance(value, tuple):
        num, den = value
        return (label, f"{num/den:.1f} EV" if den else str(value))

    if label in ("XResolution", "YResolution") and isinstance(value, tuple):
        num, den = value
        return (label, f"{num/den:.0f}" if den else str(value))

    if label == "DigitalZoomRatio" and isinstance(value, tuple):
        num, den = value
        ratio = num / den if den else 0
        return (label, f"{ratio:.1f}x" if ratio > 0 else "None")

    if isinstance(value, int) and label not in (
        "Orientation", "YCbCrPositioning", "ExposureProgram",
        "MeteringMode", "LightSource", "SensingMethod",
        "WhiteBalance", "SceneCaptureType", "SubjectDistanceRange",
    ):
        return (label, str(value))

    # Skip generic numeric codes that aren't useful
    if isinstance(value, int):
        return None

    return (label, str(value))


# ── Video ───────────────────────────────────────────────────

def _video_metadata(path: Path) -> dict:
    raw = _ffprobe(path)
    if not raw:
        return {}
    return _basic_video_fields(raw)


def _extended_video_metadata(path: Path) -> dict:
    raw = _ffprobe(path)
    if not raw:
        return {}
    info = _basic_video_fields(raw)
    extra = _extra_stream_fields(raw)
    info.update(extra)
    return info


def _basic_video_fields(raw: dict) -> dict:
    info = {}
    fmt = raw.get("format", {})
    streams = raw.get("streams", [])

    duration = fmt.get("duration")
    if duration:
        info["Duration"] = _fmt_duration(float(duration))

    size = fmt.get("size")
    if size:
        info["File Size"] = _fmt_size(int(size))

    bitrate = fmt.get("bit_rate")
    if bitrate:
        info["Overall Bitrate"] = _fmt_bitrate(int(bitrate))

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video:
        info["Video Codec"] = video.get("codec_name", "?").upper()
        w, h = video.get("width", "?"), video.get("height", "?")
        info["Resolution"] = f"{w} &#x00D7; {h}"
        fps = video.get("r_frame_rate", "?")
        if fps != "?" and "/" in fps:
            try:
                num, den = fps.split("/")
                fps = f"{float(num)/float(den):.2f}"
            except (ValueError, ZeroDivisionError):
                pass
        info["FPS"] = str(fps)

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio:
        info["Audio Codec"] = audio.get("codec_name", "?").upper()
        sr = audio.get("sample_rate")
        if sr:
            info["Sample Rate"] = f"{int(sr):,} Hz"
        ch = audio.get("channels")
        if ch is not None:
            info["Channels"] = str(ch)

    return info


def _extra_stream_fields(raw: dict) -> dict:
    info = {}
    fmt = raw.get("format", {})
    streams = raw.get("streams", [])

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video:
        for k, label in [
            ("pix_fmt", "Pixel Format"),
            ("color_space", "Color Space"),
            ("color_primaries", "Color Primaries"),
            ("color_transfer", "Color Transfer"),
            ("field_order", "Field Order"),
            ("profile", "Profile"),
            ("level", "Level"),
            ("codec_tag_string", "Codec Tag"),
        ]:
            v = video.get(k)
            if v and str(v) not in ("unknown", "0", ""):
                info[label] = str(v).upper() if isinstance(v, str) else str(v)

        tags = video.get("tags", {})
        for k, label in [("language", "Video Language"), ("encoder", "Video Encoder")]:
            v = tags.get(k)
            if v:
                info[label] = str(v)

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio:
        for k, label in [
            ("sample_fmt", "Sample Format"),
            ("profile", "Audio Profile"),
            ("codec_tag_string", "Audio Codec Tag"),
        ]:
            v = audio.get(k)
            if v and str(v) not in ("unknown", "0", ""):
                info[label] = str(v).upper() if isinstance(v, str) else str(v)

        ch_layout = audio.get("channel_layout")
        if ch_layout:
            info["Channel Layout"] = str(ch_layout)

        tags = audio.get("tags", {})
        for k, label in [("language", "Audio Language"), ("encoder", "Audio Encoder")]:
            v = tags.get(k)
            if v:
                info[label] = str(v)

    fmt_tags = fmt.get("tags", {})
    for k, label in [
        ("encoder", "Encoder"),
        ("creation_time", "Creation Time"),
    ]:
        v = fmt_tags.get(k)
        if v:
            info[label] = str(v)

    return info


# ── Audio ───────────────────────────────────────────────────

def _audio_metadata(path: Path) -> dict:
    raw = _ffprobe(path)
    if not raw:
        return {}
    return _basic_audio_fields(raw)


def _extended_audio_metadata(path: Path) -> dict:
    raw = _ffprobe(path)
    if not raw:
        return {}
    info = _basic_audio_fields(raw)
    extra = _extra_audio_fields(raw)
    info.update(extra)
    return info


def _basic_audio_fields(raw: dict) -> dict:
    info = {}
    fmt = raw.get("format", {})
    streams = raw.get("streams", [])

    duration = fmt.get("duration")
    if duration:
        info["Duration"] = _fmt_duration(float(duration))

    size = fmt.get("size")
    if size:
        info["File Size"] = _fmt_size(int(size))

    bitrate = fmt.get("bit_rate")
    if bitrate:
        info["Bitrate"] = _fmt_bitrate(int(bitrate))

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio:
        info["Audio Codec"] = audio.get("codec_name", "?").upper()
        sr = audio.get("sample_rate")
        if sr:
            info["Sample Rate"] = f"{int(sr):,} Hz"
        ch = audio.get("channels")
        if ch is not None:
            info["Channels"] = str(ch)

    return info


def _extra_audio_fields(raw: dict) -> dict:
    info = {}
    streams = raw.get("streams", [])
    fmt = raw.get("format", {})

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio:
        ch_layout = audio.get("channel_layout")
        if ch_layout:
            info["Channel Layout"] = str(ch_layout)

        for k, label in [
            ("sample_fmt", "Sample Format"),
            ("profile", "Profile"),
            ("codec_tag_string", "Codec Tag"),
        ]:
            v = audio.get(k)
            if v and str(v) not in ("unknown", "0", ""):
                info[label] = str(v).upper() if isinstance(v, str) else str(v)

        tags = audio.get("tags", {})
        for k, label in [("language", "Language"), ("encoder", "Encoder")]:
            v = tags.get(k)
            if v:
                info[label] = str(v)

    fmt_tags = fmt.get("tags", {})
    for k, label in [("encoder", "Encoder"), ("creation_time", "Creation Time")]:
        v = fmt_tags.get(k)
        if v:
            info[label] = str(v)

    return info


def get_dpi(filepath: Path, mime_type: str) -> int | None:
    if mime_type.startswith("image/"):
        try:
            with Image.open(filepath) as img:
                dpi = img.info.get("dpi")
                if dpi and len(dpi) >= 2 and dpi[0] and dpi[0] > 0:
                    return round(dpi[0])
        except Exception:
            pass
    return None


def get_dimensions(filepath: Path, mime_type: str) -> tuple[int | None, int | None]:
    if mime_type.startswith("image/"):
        try:
            with Image.open(filepath) as img:
                return img.width, img.height
        except Exception:
            return None, None
    if mime_type.startswith("video/"):
        raw = _ffprobe(filepath)
        if raw:
            video = next((s for s in raw.get("streams", []) if s.get("codec_type") == "video"), None)
            if video:
                return video.get("width"), video.get("height")
    return None, None


# ── ffprobe ─────────────────────────────────────────────────

def _ffprobe(path: Path) -> dict | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


# ── Formatters ──────────────────────────────────────────────

def _fmt_duration(secs: float) -> str:
    h, r = divmod(int(secs), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _fmt_size(bytes_: int) -> str:
    kb = bytes_ / 1024
    if kb < 1024:
        return f"{kb:.1f} KB"
    return f"{kb/1024:.1f} MB"


def _fmt_bitrate(bps: int) -> str:
    kbps = bps / 1000
    if kbps < 1000:
        return f"{kbps:.0f} kbps"
    return f"{kbps/1000:.1f} mbps"
