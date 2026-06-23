# Changelog

## 0.1.1 — Alpha

### Added
- Converted files now show the download button immediately after page refresh
- Hover tooltips on the convert button show whether it will convert, retry, or download
- Output filenames now use a unique identifier to prevent collisions

### Fixed
- Photos from phones and cameras no longer come out sideways after conversion
- Batch converting many files no longer risks running out of memory (files are processed one at a time)
- Old output files are removed before re-converting a file

### Changed
- Improved internal defaults for all image categories
- Various reliability improvements and code cleanup

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
