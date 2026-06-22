# Changelog

## 0.1.0 — Alpha

### Added
- Initial alpha release
- Image conversion: JPEG, PNG, WebP, AVIF, BMP, TIFF, GIF, QOI, ICO
- Group-level and per-file conversion settings with modal UI
- Quality slider, resize (Size/Scale modes), aspect ratio lock
- Strip metadata, preserve transparency, file size goal (JPEG only)
- Drag-and-drop upload with auto-categorization
- Filter pills (All / Photos)
- Dark teal/cyan WebUI built with FastAPI + Jinja2 + HTMX
- Fully containerized via Docker, zero host dependencies
- GPL v3 license

### Known Limitations
- Video and audio conversion are locked (coming in a future release)
- File size goal only works with JPEG output
