# Changelog

## 0.4.0 — Beta

### Added
- Audio converter rebuilt from scratch: 11 formats (MP3, AAC, FLAC, OGG, WAV, Opus, M4A, ALAC, AIFF, AC3, WMA)
- Audio group settings tab alongside Images tab
- Per-file audio settings modal with auto-fill for sample rate, bitrate, channels, duration
- Quality slider (1-100) maps linearly to format-specific bitrate ranges
- Manual bitrate toggle with plain kbps input
- Lossless formats (FLAC, WAV, ALAC, AIFF) disable quality slider
- Sample rate dropdown with high-rate warning hint
- Channels dropdown (Default / Mono / Stereo)
- Trim controls with auto-filled end time
- Bitrate ceiling clamping, scales max bitrate by sample rate and channels to prevent ffmpeg rejection
- Friendly error messages for sample rate and bitrate failures
- OGG file recognition fix (application/ogg MIME type)

### Fixed
- Convert All stuck on "Converting..." when a file errors out
- Toast repeatedly resetting on failed conversions
- High sample rates / mono + low sample rate causing silent ffmpeg failures
- File entries squishing in the UI (flex-shrink: 0)

## 0.3.0 — Alpha

### Added
- DPI support! Set dots per inch for output (JPEG, PNG, TIFF)
- Original DPI auto-detected and pre-filled on per-file settings
- DPI field in both group-level and per-file override settings

### Changed
- Audio converter removed entirely; video converter stubs removed
- Cleaned up all audio/video code from endpoints, models, and templates
- Both will be rebuilt from scratch in a future release

## 0.2.0 — Alpha

### Added
- HEIC/HEIF input support (iPhone photos) libheif-dev + format list
- Drag-and-drop file reorder with teal insertion-line indicator
- Storage tracker showing upload/output disk usage
- Download All as ZIP button
- Convert All confirmation modal
- Progress bars per file entry (3px animated bar)
- Metadata modal scrollable when EXIF is lengthy
- Banner logo replacing text header

### Fixed
- Drag-and-drop not working after uploading new files (event delegation fix)
- GIF to JPEG conversion failing (first-frame selector now applies to GIF too)
- Duplicate filenames silently overwriting each other in ZIP download
- Progress bar too slow for fast conversions (pulse 1.5s→0.1s, pre-subprocess 50%)
- Poll interval too slow to catch quick conversions (1500ms→500ms)
- UI showing stale state on page refresh mid-conversion (converting class + auto-poll)

### Changed
- Drop indicator replaced entire-entry border with thin teal line at exact insertion point

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
