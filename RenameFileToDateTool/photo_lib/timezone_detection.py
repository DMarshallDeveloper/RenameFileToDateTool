"""Per-file timezone detection from EXIF/QuickTime metadata, with NZ as the fallback.

Used both when writing (so an overseas-shot video's UTC tags are computed with the
correct offset, not always NZ) and when reading (so an existing video's true local
capture time can be recovered from its UTC tags).
"""

import re
from datetime import datetime, timedelta, timezone

from dateutil import parser, tz

from photo_lib.config import LOCAL_TIMEZONE_NAME

# Files with no embedded TZ info are assumed to have been taken in the
# configured local timezone (see photo_lib/config.py).
LOCAL_TIMEZONE = tz.gettz(LOCAL_TIMEZONE_NAME)

# Matches the ``+HH:MM`` / ``-HH:MM`` suffix exiftool emits on TZ-aware date tags.
TZ_OFFSET_RE = re.compile(r'([+-])(\d{2}):(\d{2})$')

# Matches exiftool's ``YYYY:MM:DD`` date prefix so we can rewrite it to ISO for dateutil.
EXIF_DATE_RE = re.compile(r'^(\d{4}):(\d{2}):(\d{2})')


def parse_tz_offset(offset_string) -> timezone | None:
    """Parse a ``+HH:MM`` or ``-HH:MM`` string into a fixed-offset timezone, or None."""
    if not offset_string:
        return None
    match = TZ_OFFSET_RE.search(str(offset_string))
    if not match:
        return None
    sign = 1 if match.group(1) == '+' else -1
    hours = int(match.group(2))
    minutes = int(match.group(3))
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def detect_file_tz(file_metadata: dict, default_tz=LOCAL_TIMEZONE):
    """Detect the photo's original local timezone from existing EXIF/QuickTime tags.

    Priority:
      1. The TZ embedded in CreationDate (Apple Keys atom — most trustworthy for video)
      2. OffsetTimeOriginal (image EXIF)
      3. OffsetTime / OffsetTimeDigitized (image EXIF fallbacks)
      4. The TZ embedded in DateTimeOriginal (if any — note: can be wrong on broken videos)
      5. Fall back to ``default_tz`` (see ``photo_lib.config.LOCAL_TIMEZONE_NAME``)
    """
    if not file_metadata:
        return default_tz

    creation_date = file_metadata.get("CreationDate")
    detected = parse_tz_offset(creation_date)
    if detected is not None:
        return detected

    for tag in ("OffsetTimeOriginal", "OffsetTime", "OffsetTimeDigitized"):
        detected = parse_tz_offset(file_metadata.get(tag))
        if detected is not None:
            return detected

    date_time_original = file_metadata.get("DateTimeOriginal")
    detected = parse_tz_offset(date_time_original)
    if detected is not None:
        return detected

    return default_tz


def parse_exif_datetime(date_time_string, treat_naive_as_utc=False, target_tz=LOCAL_TIMEZONE) -> datetime | None:
    """Parse an exiftool date string and return a naive datetime in ``target_tz``.

    exiftool emits dates like ``YYYY:MM:DD HH:MM:SS`` optionally followed by a
    ``+HH:MM`` timezone offset and/or ``.SSS`` subseconds.
      - If a timezone offset is present, convert to ``target_tz``.
      - If no offset is present and ``treat_naive_as_utc`` is True (use for QuickTime/MP4
        container tags like MediaCreateDate, which are stored as UTC by spec),
        interpret as UTC and convert to ``target_tz``.
      - Otherwise treat as already in ``target_tz``.
    """
    s = str(date_time_string).strip()
    s = s.split(".")[0].strip()  # drop subseconds but keep TZ
    s = EXIF_DATE_RE.sub(r'\1-\2-\3', s, count=1)

    try:
        dt = parser.parse(s)
    except (ValueError, TypeError):
        return None

    if dt.tzinfo is not None:
        return dt.astimezone(target_tz).replace(tzinfo=None)
    if treat_naive_as_utc:
        return dt.replace(tzinfo=timezone.utc).astimezone(target_tz).replace(tzinfo=None)
    return dt
