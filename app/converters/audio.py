import asyncio
import uuid
from pathlib import Path

from ..storage import UPLOADS_DIR
from . import OUTPUT_DIR, update_progress, set_done, set_error

FORMAT_EXT = {
    "MP3": ".mp3", "AAC": ".m4a", "FLAC": ".flac",
    "OGG": ".ogg", "WAV": ".wav", "Opus": ".opus",
    "M4A": ".m4a", "ALAC": ".m4a", "AIFF": ".aiff",
    "AC3": ".ac3", "WMA": ".wma",
}

LOSSLESS_FORMATS = {"FLAC", "WAV", "ALAC", "AIFF"}

BITRATE_RANGES = {
    "MP3": (32, 320), "AAC": (32, 320), "OGG": (64, 320),
    "Opus": (6, 256), "M4A": (32, 320), "AC3": (32, 640),
    "WMA": (32, 320),
}

CODEC_MAP = {
    "MP3": "libmp3lame", "AAC": "aac", "FLAC": "flac",
    "OGG": "libvorbis", "WAV": "pcm_s16le", "Opus": "libopus",
    "M4A": "aac", "ALAC": "alac", "AIFF": "pcm_s16le",
    "AC3": "ac3", "WMA": "wmav2",
}


async def _progress_pulse(file_id: str):
    pct = 10
    while pct < 80:
        update_progress(file_id, pct)
        pct += 5
        await asyncio.sleep(0.5)


def _user_error(stderr: str) -> str:
    lower = stderr.lower()
    if "sample rate" in lower:
        return "Conversion failed. High sample rates (96000/192000 Hz) may fail with lossy formats."
    if "bitrate" in lower:
        return "Conversion failed. Bitrate too high for this format at the selected sample rate and channels. Mono and low sample rates need less bitrate - try lowering quality or using Stereo."
    return f"Conversion failed:\n{stderr[:300]}"


def _quality_to_bitrate(fmt: str, quality: int) -> int:
    if fmt in LOSSLESS_FORMATS or fmt not in BITRATE_RANGES:
        return 0
    lo, hi = BITRATE_RANGES[fmt]
    if quality <= 1:
        return lo
    if quality >= 100:
        return hi
    return lo + round((quality - 1) / 99 * (hi - lo))


def _bitrate_ceiling(fmt: str, sample_rate: str, channels: str) -> int:
    if fmt in LOSSLESS_FORMATS or fmt not in BITRATE_RANGES:
        return 0
    lo, hi = BITRATE_RANGES[fmt]
    ratio = 1.0
    if sample_rate:
        ratio *= min(1.0, int(sample_rate) / 44100.0)
    if channels:
        ratio *= min(1.0, int(channels) / 2.0)
    return max(lo, round(hi * ratio))


async def convert(file_id: str, entry: dict, settings: dict):
    try:
        ext = Path(entry["filename"]).suffix
        input_path = UPLOADS_DIR / f"{file_id}{ext}"

        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        update_progress(file_id, 5)

        output_format = settings.get("output_format", "MP3")
        output_ext = FORMAT_EXT.get(output_format, ".mp3")
        output_path = OUTPUT_DIR / f"{file_id}_{uuid.uuid4().hex[:12]}{output_ext}"

        cmd = ["ffmpeg", "-i", str(input_path), "-y"]

        if settings.get("strip_metadata"):
            cmd.extend(["-map_metadata", "-1"])

        # Probe source for fallback sample rate / channels
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=sample_rate,channels",
            "-of", "default=noprint_wrappers=1",
            str(input_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *probe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        probe_out, _ = await proc.communicate()
        orig_info = {}
        for line in probe_out.decode().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                orig_info[k.strip()] = v.strip()
        orig_sr = orig_info.get("sample_rate", "")
        orig_ch = orig_info.get("channels", "")

        sample_rate = settings.get("sample_rate", "") or orig_sr
        channels = settings.get("channels", "") or orig_ch

        ceiling = _bitrate_ceiling(output_format, sample_rate, channels)

        if settings.get("manual_bitrate") and settings.get("bitrate"):
            br_val = settings["bitrate"].rstrip("kK")
            if ceiling > 0:
                br_val = str(min(int(br_val), ceiling))
            cmd.extend(["-b:a", f"{br_val}k"])
        elif output_format not in LOSSLESS_FORMATS:
            quality = settings.get("quality", 100)
            br = _quality_to_bitrate(output_format, quality)
            if br > 0:
                if ceiling > 0:
                    br = min(br, ceiling)
                cmd.extend(["-b:a", f"{br}k"])

        codec = CODEC_MAP.get(output_format)
        if codec:
            cmd.extend(["-c:a", codec])

        if sample_rate:
            cmd.extend(["-ar", sample_rate])

        if channels:
            cmd.extend(["-ac", channels])

        start_time = settings.get("start_time")
        if start_time:
            cmd.extend(["-ss", start_time])

        end_time = settings.get("end_time")
        if end_time:
            cmd.extend(["-to", end_time])

        cmd.append(str(output_path))

        update_progress(file_id, 10)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        pulse = asyncio.create_task(_progress_pulse(file_id))
        _, stderr_data = await proc.communicate()
        pulse.cancel()

        if proc.returncode != 0:
            err_text = stderr_data.decode("utf-8", errors="replace").strip()
            raise RuntimeError(_user_error(err_text) or "ffmpeg failed")

        if not output_path.exists():
            raise RuntimeError("Output file was not created")

        update_progress(file_id, 90)
        set_done(file_id, str(output_path))

    except Exception as e:
        set_error(file_id, str(e))
