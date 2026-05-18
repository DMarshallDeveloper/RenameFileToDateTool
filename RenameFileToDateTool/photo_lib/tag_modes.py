"""Mapping from exiftool tag → how its date should be formatted when written.

Modes:
  - ``local``         — naive local-time string (image EXIF tags, filesystem dates)
  - ``utc``           — convert local→UTC, naive (QuickTime container tags)
  - ``local_with_tz`` — local time with ``+HH:MM`` offset (Apple Keys CreationDate)

Why separate dicts for image vs video: QuickTime ``MediaCreateDate``/``TrackCreateDate``
are *spec'd as UTC* in the container, while the Apple Keys atom ``CreationDate``
explicitly carries the local time plus a TZ offset. Image EXIF (``DateTimeOriginal``)
is always local, sometimes with a companion ``OffsetTimeOriginal``. Mismatches between
these — e.g. setting CreationDate to UTC instead of local-with-offset — make videos
display wrong dates in Windows Explorer / Photos / Google Photos.
"""

from datetime import timezone

IMAGE_TAG_MODES = {
    "DateTimeOriginal": 'local',
    "CreateDate":       'local',
    "DateCreated":      'local',
    "ModifyDate":       'local',
    "FileCreateDate":   'local',
    "FileModifyDate":   'local',
}

VIDEO_TAG_MODES = {
    "MediaCreateDate":  'utc',
    "MediaModifyDate":  'utc',
    "TrackCreateDate":  'utc',
    "TrackModifyDate":  'utc',
    "CreateDate":       'utc',
    "ModifyDate":       'utc',
    "CreationDate":     'local_with_tz',
    "FileCreateDate":   'local',
    "FileModifyDate":   'local',
}

# Tags whose stored value is meant to be a local-time wallclock (no offset, no UTC).
# Filesystem dates are reported by exiftool with a +HH:MM suffix on Windows but the
# semantically meaningful part is the local-time portion.
FILESYSTEM_TAGS = ("FileCreateDate", "FileModifyDate")


def format_date_for_mode(date_time, mode, file_tz) -> str:
    """Format a naive local datetime per the tag's mode (see module docstring).

    ``file_tz``: the photo's local timezone (used for 'utc' and 'local_with_tz' modes).
    ``local`` mode ignores the TZ and just writes the naive datetime.
    """
    if mode == 'utc':
        aware = date_time.replace(tzinfo=file_tz).astimezone(timezone.utc)
        return aware.strftime("%Y:%m:%d %H:%M:%S")
    if mode == 'local_with_tz':
        aware = date_time.replace(tzinfo=file_tz)
        offset = aware.strftime("%z")  # e.g. "+1300"
        offset_with_colon = f"{offset[:3]}:{offset[3:]}"  # "+13:00"
        return aware.strftime("%Y:%m:%d %H:%M:%S") + offset_with_colon
    return date_time.strftime("%Y:%m:%d %H:%M:%S")
