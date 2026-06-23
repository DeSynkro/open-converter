import asyncio
import uuid
from pathlib import Path

from ..storage import UPLOADS_DIR
from . import OUTPUT_DIR, update_progress, set_done, set_error

FORMAT_EXT = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WebP": ".webp",
    "AVIF": ".avif",
    "HEIC": ".heic",
    "BMP": ".bmp",
    "TIFF": ".tiff",
    "GIF": ".gif",
    "QOI": ".qoi",
    "ICO": ".ico",
}

LOSSLESS_FORMATS = {"PNG", "BMP", "TIFF", "GIF", "QOI", "ICO"}

async def _progress_pulse(file_id: str):
    pct = 35
    while pct < 85:
        update_progress(file_id, pct)
        pct += 10
        await asyncio.sleep(1.5)


async def convert(file_id: str, entry: dict, settings: dict):
    try:
        ext = Path(entry["filename"]).suffix
        input_path = UPLOADS_DIR / f"{file_id}{ext}"

        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        update_progress(file_id, 10)

        output_format = settings.get("output_format", "JPEG")
        output_ext = FORMAT_EXT.get(output_format, ".jpg")
        output_path = OUTPUT_DIR / f"{file_id}_{uuid.uuid4().hex[:12]}{output_ext}"

        update_progress(file_id, 20)

        input_arg = str(input_path)
        if ext.lower() == ".ico" and output_format != "ICO":
            input_arg += "[0]"
        cmd = ["convert", input_arg]

        if settings.get("strip_metadata"):
            cmd.append("-strip")

        if not settings.get("preserve_transparency", True):
            cmd.extend(["-alpha", "off"])

        if settings.get("quality") is not None and output_format not in LOSSLESS_FORMATS:
            cmd.extend(["-quality", str(settings["quality"])])

        cmd.append("-auto-orient")

        if settings.get("use_scale"):
            scale_pct = settings.get("scale_pct")
            if scale_pct:
                cmd.extend(["-resize", f"{scale_pct}%"])
        else:
            mw = settings.get("max_width")
            mh = settings.get("max_height")
            if mw or mh:
                resize_val = f"{mw or ''}{'x'}{mh or ''}"
                if mw and mh and not settings.get("aspect_locked", True):
                    resize_val += "!"
                else:
                    resize_val += ">"
                cmd.extend(["-resize", resize_val])

        if settings.get("file_size_goal"):
            value = settings.get("file_size_value")
            unit = settings.get("file_size_unit", "MB")
            if value:
                if output_format == "JPEG":
                    cmd.extend(["-define", f"jpeg:extent={value}{unit}"])

        cmd.append(str(output_path))

        update_progress(file_id, 30)

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
            raise RuntimeError(err_text or "ImageMagick failed")

        if not output_path.exists():
            raise RuntimeError("Output file was not created")

        update_progress(file_id, 90)
        set_done(file_id, str(output_path))

    except Exception as e:
        set_error(file_id, str(e))
