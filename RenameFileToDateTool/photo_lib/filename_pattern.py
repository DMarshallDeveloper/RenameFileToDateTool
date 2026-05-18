"""Canonical filename-date pattern shared by every script that reads dates back out
of filenames.

The master library's filename convention is ``YYYY-MM-DD HH.MM.SS_N.ext`` where:
  - the date/time separator is a space (NOT an underscore; underscore is an older
    Google-takeout-script format we accept on read for back-compatibility)
  - the time separator is a dot (some old files used a hyphen — accepted on read)
  - ``_N`` is a 1-indexed counter that makes filenames unique even for same-second photos

Before this module, the same regex existed in 5 different forms across the codebase.
"""

import os
import re
from datetime import datetime

# Group 1 = date part (YYYY-MM-DD), group 2 = time part (HH.MM.SS or HH-MM-SS)
# Anchored to start so a stem like "foo-2026-04-09 19.52.51_1.jpg" won't accidentally match.
FILENAME_DATE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})[ _](\d{2}[.\-]\d{2}[.\-]\d{2})')

# Same shape but groups split, used by the year-extractor and the audit script
FILENAME_PARTS_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})[ _](\d{2})[.\-](\d{2})[.\-](\d{2})')

# Loose form that accepts HH.MM (no seconds). Used as a secondary fallback so a file
# named ``2026-04-09 19.52_1.jpg`` (older script output) still gets a sensible date.
FILENAME_DATE_NO_SECONDS_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})[ _](\d{2}[.\-]\d{2})(?!\d)')

# Year-only matcher, used by IngestInboxToMaster.py
FILENAME_YEAR_RE = re.compile(r'^(\d{4})-\d{2}-\d{2}')

# Strict "fully canonical" pattern: with _N suffix and 3-4-char extension.
# DetectMalformedFileNames.py uses this to flag anything that drifted off-spec.
CANONICAL_FILENAME_RE = re.compile(
    r'^\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2}_\d+\.[a-zA-Z0-9]{3,4}$'
)

# Placeholder filenames: YYYY-01-01 00.00.00 means "we don't know the time of year".
# main.py bumps these to 13:00 NZ so they don't roll back to Dec 31 in UTC viewers.
PLACEHOLDER_FILENAME_RE = re.compile(r'^\d{4}-01-01 00\.00\.00')


def parse_filename_datetime(filename: str) -> datetime | None:
    """Pull a datetime from the start of ``filename``.

    Tries HH.MM.SS first, then falls back to HH.MM (seconds default to 0). Returns
    None if neither form matches. Accepts both ``HH.MM.SS`` and ``HH-MM-SS`` (the
    older takeout-script format used hyphens).
    """
    match = FILENAME_DATE_RE.search(filename)
    if match:
        date_part = match.group(1)
        time_part = match.group(2).replace("-", ".")
        try:
            return datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H.%M.%S")
        except ValueError:
            return None

    match = FILENAME_DATE_NO_SECONDS_RE.search(filename)
    if match:
        date_part = match.group(1)
        time_part = match.group(2).replace("-", ".")
        try:
            return datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H.%M")
        except ValueError:
            return None

    return None


def parse_filename_year(filename: str) -> int | None:
    """Pull the year integer from the start of ``filename``, or None."""
    match = FILENAME_YEAR_RE.match(filename)
    if not match:
        return None
    year = int(match.group(1))
    if year < 1900 or year > 2100:
        return None
    return year


def apply_placeholder_time_bump(filename: str, date_time: datetime) -> datetime:
    """Bump placeholder ``YYYY-01-01 00.00.00`` dates to 13:00 NZ-equivalent (1pm).

    Why: midnight Jan 1 in NZ is Dec 31 in UTC, so any UTC-respecting viewer rolls
    the photo back to the previous year. 13:00 NZ NZDT (UTC+13) = 00:00 UTC exactly,
    so the date lands cleanly on Jan 1 in every viewer.
    """
    if PLACEHOLDER_FILENAME_RE.match(filename):
        return date_time.replace(hour=13, minute=0, second=0)
    return date_time


def maybe_rename_placeholder(file_path: str, dry_run: bool = False) -> str | None:
    """Rename a placeholder file's basename so its filename matches the EXIF
    that ``apply_placeholder_time_bump`` produces.

    Files named ``YYYY-01-01 00.00.00_N.ext`` would otherwise end up with EXIF
    ``13:00:00`` (after the bump) but filename ``00.00.00`` — a confusing
    asymmetry. This helper renames them in-place to ``YYYY-01-01 13.00.00_N.ext``
    so the filename ≡ EXIF invariant always holds.

    Returns the (possibly-new) path on success, or ``None`` if a rename was
    needed but the target name already exists (caller should skip such files
    to preserve the invariant).

    Idempotent: a file already named ``13.00.00`` doesn't match the placeholder
    regex, so the function leaves it alone.
    """
    filename = os.path.basename(file_path)
    if not PLACEHOLDER_FILENAME_RE.match(filename):
        return file_path

    new_filename = filename.replace(' 00.00.00', ' 13.00.00', 1)
    if new_filename == filename:
        return file_path  # defensive: regex matched but substitution didn't change anything
    new_path = os.path.join(os.path.dirname(file_path), new_filename)

    if os.path.exists(new_path) and os.path.abspath(new_path) != os.path.abspath(file_path):
        return None  # collision

    if not dry_run:
        os.rename(file_path, new_path)
    return new_path
