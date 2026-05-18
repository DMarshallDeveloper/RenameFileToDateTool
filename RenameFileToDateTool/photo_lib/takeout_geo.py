"""GPS → timezone resolution for Google Takeout JSON metadata.

When importing photos from a Takeout dump, ``photoTakenTime.timestamp`` is UTC. To
turn that into a sensible local-time filename for an overseas-shot photo, we use the
JSON's geoData lat/lon to derive the photo's true TZ — falling back to NZ when no
GPS is present.

Without this, every overseas photo would land in the master library with a NZ-shifted
filename, off by 2–13 hours from the time on the camera.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from photo_lib.config import LOCAL_TIMEZONE_NAME

DEFAULT_TIMEZONE = ZoneInfo(LOCAL_TIMEZONE_NAME)

# timezonefinder loads ~50 MB of polygon data; build once and reuse.
_timezone_finder_instance = None


def _get_timezone_finder():
    global _timezone_finder_instance
    if _timezone_finder_instance is None:
        from timezonefinder import TimezoneFinder
        _timezone_finder_instance = TimezoneFinder()
    return _timezone_finder_instance


def resolve_timezone_from_geo(metadata: dict) -> ZoneInfo | None:
    """Look up the photo's local TZ from its Takeout geoData block.

    Google Takeout JSONs carry two GPS blocks: ``geoDataExif`` (original camera EXIF)
    and ``geoData`` (Google-enriched, sometimes inferred from album/upload). We prefer
    the EXIF block — it reflects where the photo was *taken*, whereas ``geoData`` can
    be inferred or overwritten by Google.

    Returns None if no usable GPS coords are present. Google writes ``(0.0, 0.0)`` in
    both lat and lon to mean "no data" — we treat that as no data, even though (0,0)
    is a real point off Africa, because no consumer camera/phone actually emits exactly
    zero.
    """
    for block_name in ("geoDataExif", "geoData"):
        block = metadata.get(block_name)
        if not isinstance(block, dict):
            continue
        latitude = block.get("latitude")
        longitude = block.get("longitude")
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            continue
        if latitude == 0 and longitude == 0:
            continue  # Takeout's "no GPS data" sentinel

        timezone_name = _get_timezone_finder().timezone_at(lat=latitude, lng=longitude)
        if timezone_name:
            try:
                return ZoneInfo(timezone_name)
            except Exception:
                continue
    return None


def local_datetime_from_metadata(metadata: dict, default_timezone: ZoneInfo = DEFAULT_TIMEZONE) -> datetime:
    """Convert Takeout's UTC ``photoTakenTime.timestamp`` into local time of capture.

    Uses the photo's GPS (via timezonefinder) when available so overseas photos keep
    their actual local time in the filename. Falls back to ``default_timezone`` (NZ)
    when no GPS.
    """
    timestamp_seconds = int(metadata["photoTakenTime"]["timestamp"])
    local_timezone = resolve_timezone_from_geo(metadata) or default_timezone
    return datetime.fromtimestamp(timestamp_seconds, tz=local_timezone)
