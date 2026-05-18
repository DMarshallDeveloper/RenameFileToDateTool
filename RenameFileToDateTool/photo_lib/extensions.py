"""Canonical media file extensions used across the photo-library scripts.

Stored WITHOUT leading dots so the same set works for both ``FileTypeExtension`` from
exiftool ("jpg") and stripped ``os.path.splitext`` results. Callers that get extensions
from ``os.path.splitext`` (which include the leading dot) should ``.lstrip('.')``
before membership-testing.

Why centralize: before this module, ~6 files each defined their own set, and they
drifted — ``write_exif_from_filename.py`` was missing heif/3gp/m4v and would silently reject those files.
"""

IMAGE_EXTENSIONS = frozenset({
    "jpg", "jpeg", "png", "gif", "heic", "heif", "tiff",
})

VIDEO_EXTENSIONS = frozenset({
    "avi", "mpg", "mpeg", "mp4", "mov", "mkv", "3gp", "m4v", "wmv",
})

MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def normalize_extension(ext: str) -> str:
    """Drop a leading dot and lowercase. Idempotent."""
    return ext.lower().lstrip(".")


def is_image(ext: str) -> bool:
    return normalize_extension(ext) in IMAGE_EXTENSIONS


def is_video(ext: str) -> bool:
    return normalize_extension(ext) in VIDEO_EXTENSIONS


def is_media(ext: str) -> bool:
    return normalize_extension(ext) in MEDIA_EXTENSIONS
