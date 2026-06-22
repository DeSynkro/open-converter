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
    file_size_goal: bool = False
    file_size_value: int | None = None
    file_size_unit: str = "MB"


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
    file_size_goal: bool | None = None
    file_size_value: int | None = None
    file_size_unit: str | None = None


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


IMAGE_FORMATS = ["JPEG", "PNG", "WebP", "AVIF", "BMP", "TIFF", "GIF", "QOI", "ICO"]

EXT_TO_FORMAT = {
    ".jpg": "JPEG", ".jpeg": "JPEG",
    ".png": "PNG", ".webp": "WebP", ".avif": "AVIF",
    ".bmp": "BMP", ".tiff": "TIFF", ".tif": "TIFF",
    ".gif": "GIF", ".qoi": "QOI", ".ico": "ICO",
}

VIDEO_FORMATS = [
    ("mp4", "MP4 (H.264)"),
    ("mp4_hevc", "MP4 (H.265/HEVC)"),
    ("webm", "WebM (VP9)"),
    ("avi", "AVI"),
    ("mkv", "MKV"),
    ("gif", "GIF"),
]

AUDIO_FORMATS = ["MP3", "AAC", "FLAC", "OGG", "WAV", "Opus", "M4A", "ALAC", "AIFF", "AC3", "WMA"]
LOSSLESS_AUDIO = {"FLAC", "WAV", "ALAC", "AIFF"}

DEFAULT_SETTINGS = {
    "image": {"output_format": "JPEG", "quality": 100, "crf": None, "bitrate": None, "max_width": None, "max_height": None},
    "video": {"output_format": "mp4", "quality": 70, "crf": 23, "bitrate": None, "max_width": None, "max_height": None},
    "audio": {"output_format": "MP3", "quality": 80, "crf": None, "bitrate": None, "max_width": None, "max_height": None},
}
