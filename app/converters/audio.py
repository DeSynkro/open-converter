import asyncio
import re
import time
from pathlib import Path

from ..storage import UPLOADS_DIR
from . import OUTPUT_DIR, update_progress, set_done, set_error

FORMAT_EXT = {
    "MP3": ".mp3",
    "AAC": ".m4a",
    "FLAC": ".flac",
    "OGG": ".ogg",
    "WAV": ".wav",
    "Opus": ".opus",
    "M4A": ".m4a",
    "ALAC": ".m4a",
    "AIFF": ".aiff",
    "AC3": ".ac3",
    "WMA": ".wma",
}

LOSSLESS_AUDIO_SET = {"FLAC", "WAV", "ALAC", "AIFF"}

FORMAT_CODEC = {
    "MP3": "libmp3lame",
    "AAC": "aac",
    "FLAC": "flac",
    "OGG": "libvorbis",
    "WAV": "pcm_s16le",
    "Opus": "libopus",
    "M4A": "aac",
    "ALAC": "alac",
    "AIFF": "pcm_s16be",
    "AC3": "ac3",
    "WMA": "wmav2",
}


def _quality_to_bitrate(q: int, fmt: str) -> str:
    if fmt == "Opus":
        br = 32 + int((q - 1) / 99 * 224)
    else:
        br = 64 + int((q - 1) / 99 * 256)
    return f"{br}k"


async def _get_duration(filepath: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(filepath),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    text = out.decode().strip()
    return float(text) if text else 0


async def convert(file_id: str, entry: dict, settings: dict):
    try:
        ext = Path(entry["filename"]).suffix
        input_path = UPLOADS_DIR / f"{file_id}{ext}"

        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        update_progress(file_id, 5)

        output_format = settings.get("output_format", "MP3")
        output_ext = FORMAT_EXT.get(output_format, ".mp3")
        output_path = OUTPUT_DIR / f"{file_id}_{int(time.time())}{output_ext}"

        update_progress(file_id, 10)

        cmd = ["ffmpeg", "-y"]

        start_time = settings.get("start_time")
        if start_time:
            cmd.extend(["-ss", start_time])

        cmd.extend(["-i", str(input_path)])

        end_time = settings.get("end_time")
        if end_time:
            cmd.extend(["-to", end_time])

        codec = FORMAT_CODEC.get(output_format)
        if codec:
            cmd.extend(["-codec:a", codec])

        if settings.get("strip_metadata"):
            cmd.extend(["-map_metadata", "-1"])

        sample_rate = settings.get("sample_rate")
        if sample_rate:
            cmd.extend(["-ar", str(sample_rate)])

        if output_format not in LOSSLESS_AUDIO_SET:
            bitrate = settings.get("bitrate")
            if not bitrate:
                quality = settings.get("quality")
                if quality is not None:
                    bitrate = _quality_to_bitrate(quality, output_format)
            if bitrate:
                cmd.extend(["-b:a", bitrate])

        cmd.append(str(output_path))

        update_progress(file_id, 15)

        duration = await _get_duration(input_path)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        last_pct = 0
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            match = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", text)
            if match and duration > 0:
                h, m, s = map(float, match.groups())
                current = h * 3600 + m * 60 + s
                pct = min(int((current / duration) * 100), 99)
                if pct > last_pct:
                    last_pct = pct
                    update_progress(file_id, pct)

        await proc.wait()

        if proc.returncode != 0:
            raise RuntimeError("ffmpeg conversion failed")

        if not output_path.exists():
            raise RuntimeError("Output file was not created")

        update_progress(file_id, 100)
        set_done(file_id, str(output_path))

    except Exception as e:
        set_error(file_id, str(e))
