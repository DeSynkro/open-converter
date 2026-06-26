from datetime import datetime, timezone

from pydantic import BaseModel


class FileEntry(BaseModel):
    id: str
    filename: str
    size: int
    mime_type: str
    category: str
    uploaded_at: str


class GroupSettings(BaseModel):
    output_format: str = ""
    quality: int = 80
    crf: int | None = None
    bitrate: str | None = None
    max_width: int | None = None
    max_height: int | None = None
    strip_metadata: bool = False
    preserve_transparency: bool = True
    use_scale: bool = False
    scale_pct: int | None = None
    aspect_locked: bool = True
    file_size_goal: bool = False
    file_size_value: int | None = None
    file_size_unit: str = "MB"
    dpi: int | None = None


class FileOverride(BaseModel):
    file_id: str
    active: bool = False
    output_format: str | None = None
    quality: int | None = None
    crf: int | None = None
    bitrate: str | None = None
    max_width: int | None = None
    max_height: int | None = None
    strip_metadata: bool | None = None
    preserve_transparency: bool | None = None
    use_scale: bool | None = None
    scale_pct: int | None = None
    aspect_locked: bool | None = None
    file_size_goal: bool | None = None
    file_size_value: int | None = None
    file_size_unit: str | None = None
    dpi: int | None = None


def categorize(mime: str) -> str:
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "other"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


IMAGE_FORMATS = ["JPEG", "PNG", "WebP", "AVIF", "HEIC", "BMP", "TIFF", "GIF", "QOI", "ICO"]

AUDIO_FORMATS = ["MP3", "AAC", "FLAC", "OGG", "WAV", "Opus", "M4A", "ALAC", "AIFF", "AC3", "WMA"]
LOSSLESS_AUDIO = {"FLAC", "WAV", "ALAC", "AIFF"}

EXT_TO_FORMAT = {
    ".jpg": "JPEG", ".jpeg": "JPEG",
    ".png": "PNG", ".webp": "WebP", ".avif": "AVIF",
    ".heic": "HEIC", ".heif": "HEIC",
    ".bmp": "BMP", ".tiff": "TIFF", ".tif": "TIFF",
    ".gif": "GIF", ".qoi": "QOI", ".ico": "ICO",
}

DEFAULT_SETTINGS = {
    "image": {
        "output_format": "JPEG", "quality": 100, "crf": None, "bitrate": None,
        "max_width": None, "max_height": None, "strip_metadata": False,
        "preserve_transparency": True, "use_scale": False, "scale_pct": None,
        "aspect_locked": True, "file_size_goal": False, "file_size_value": None,
        "file_size_unit": "MB", "dpi": None,
    },
    "audio": {
        "output_format": "MP3", "quality": 100, "crf": None, "bitrate": None,
        "manual_bitrate": False, "sample_rate": "", "channels": "",
        "strip_metadata": False,
    },
}
