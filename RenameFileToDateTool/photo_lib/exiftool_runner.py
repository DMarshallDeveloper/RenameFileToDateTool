"""Thin wrappers around exiftool that handle the Windows command-line length limit
by routing long file lists through a ``-@ filelist`` temp file.

Without the filelist trick, scripts that operated on year folders with thousands of
files would silently hit the 32k command-line cap and exiftool would either truncate
or fail in confusing ways.
"""

import csv
import io
import json
import logging
import os
import re
import subprocess
import tempfile

from photo_lib.binaries import EXIFTOOL
from photo_lib.tag_modes import format_date_for_mode

# Exiftool can append a "+HH:MM" or "-HH:MM" offset on naive tags (e.g. filesystem
# dates on Windows). For "is this in sync?" comparison we strip the suffix so a
# Windows-side "+13:00" doesn't make us think the file needs re-writing.
_TZ_SUFFIX_RE = re.compile(r'[+-]\d{2}:\d{2}$')

# Tags that get written but don't round-trip cleanly through exiftool (e.g. XMP
# DateCreated is stored date-only in some namespaces, so reading it back never
# matches the full HH:MM:SS we wrote). Exclude from sync checking — write them
# unconditionally as part of the batch.
_SYNC_CHECK_EXCLUDED_TAGS = frozenset({"DateCreated"})

# Library code logs via the shared 'photo_lib' logger; callers that ran
# configure_logging() get these messages on console + in the log file. Callers
# that didn't will see nothing here (the default logger has no handlers),
# which is fine for the legacy 'just print' style.
_logger = logging.getLogger("photo_lib")


def get_all_metadata(file_paths) -> dict:
    """Run exiftool against all files in one call (via ``-@ filelist``) and return
    a dict of ``FileName`` (basename) → metadata dict.
    """
    metadata_by_name = {}
    if not file_paths:
        return metadata_by_name

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                     encoding='utf-8') as list_tmp:
        for p in file_paths:
            list_tmp.write(p + '\n')
        list_path = list_tmp.name

    try:
        result = subprocess.run(
            [EXIFTOOL, '-json', '-@', list_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding='utf-8', errors='replace'
        )
        try:
            metadata_list = json.loads(result.stdout) if result.stdout else []
        except (json.JSONDecodeError, ValueError, TypeError):
            metadata_list = []
        for m in metadata_list:
            metadata_by_name[m.get('FileName', '')] = m
    finally:
        os.unlink(list_path)

    return metadata_by_name


def get_metadata_for_tags(file_paths, tags) -> list:
    """Like ``get_all_metadata`` but restricted to a specific tag set, returning
    the raw list of per-file metadata dicts (each with a ``SourceFile`` key).

    Useful for audit-style reads where you want full paths back as keys, not basenames.
    """
    if not file_paths:
        return []

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                     encoding='utf-8') as list_tmp:
        for p in file_paths:
            list_tmp.write(p + '\n')
        list_path = list_tmp.name

    try:
        cmd = [EXIFTOOL, '-json'] + [f'-{t}' for t in set(tags)] + ['-@', list_path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                encoding='utf-8', errors='replace')
        try:
            return json.loads(result.stdout) if result.stdout else []
        except (json.JSONDecodeError, ValueError, TypeError):
            return []
    finally:
        os.unlink(list_path)


def is_metadata_in_sync(file_metadata: dict, expected_dt, file_tz, attribute_modes: dict) -> bool:
    """Return True if every tag in ``attribute_modes`` already has the value
    ``format_date_for_mode`` would produce for ``(expected_dt, file_tz, mode)``.

    Used by Mode 1 (the EXIF writer) to skip files that are already in the desired
    state — avoiding the unnecessary write (and mtime bump / Google Drive resync)
    of files we've already fixed.

    Conservative: any missing tag, parse failure, or value mismatch causes a False
    return, so the caller writes. Writing when not strictly needed is harmless
    (it's idempotent); skipping when it WAS needed would be a silent correctness bug.
    """
    for tag, mode in attribute_modes.items():
        if tag in _SYNC_CHECK_EXCLUDED_TAGS:
            continue
        expected = format_date_for_mode(expected_dt, mode, file_tz)
        actual = file_metadata.get(tag)
        if actual is None:
            return False
        actual_str = str(actual)
        if mode in ('utc', 'local'):
            actual_str = _TZ_SUFFIX_RE.sub('', actual_str).strip()
        if actual_str != expected:
            return False
    return True


WRITE_BATCH_CHUNK_SIZE = 100
WRITE_BATCH_TIMEOUT_SECONDS = 1200


def write_exif_dates_batch(file_date_map, attribute_modes, error_log=None,
                           chunk_size: int = WRITE_BATCH_CHUNK_SIZE) -> None:
    """Write date attributes for the given files via exiftool ``-csv``, chunked.

    ``file_date_map``: dict ``file_path → (datetime, tzinfo)``. The tzinfo is the
                       photo's local timezone (detected per-file, NZ fallback).
    ``attribute_modes``: dict ``exiftool tag name → format mode`` — see
                         ``photo_lib.tag_modes.format_date_for_mode``.
    ``error_log``: deprecated. Pass nothing — diagnostics now go through the
                   shared ``photo_lib`` logger. The parameter is kept for back-compat
                   with old callers; if a file-like is provided, its writes are
                   mirrored there as well.
    ``chunk_size``: files per exiftool invocation. The 600s subprocess timeout
                    was reached on a 12689-file batch (~21 files/sec) when this
                    was unchunked. Smaller chunks finish well under the timeout
                    and a single failing chunk doesn't lose the whole sweep.
    """
    if not file_date_map:
        return

    all_paths = list(file_date_map.keys())
    total_files = len(all_paths)
    if total_files > chunk_size:
        _logger.info("Chunking %d files into batches of %d", total_files, chunk_size)

    for chunk_start in range(0, total_files, chunk_size):
        chunk_paths = all_paths[chunk_start:chunk_start + chunk_size]
        chunk_index = chunk_start // chunk_size + 1
        chunk_total = (total_files + chunk_size - 1) // chunk_size
        if chunk_total > 1:
            _logger.info("Batch %d/%d: writing %d files...",
                         chunk_index, chunk_total, len(chunk_paths))
        chunk_file_date_map = {path: file_date_map[path] for path in chunk_paths}
        _write_exif_dates_single_batch(chunk_file_date_map, attribute_modes, error_log)


def _write_exif_dates_single_batch(file_date_map, attribute_modes, error_log) -> None:
    """One exiftool invocation. Kept private so callers go through the chunked
    wrapper above and inherit the per-call timeout safety."""
    attributes = list(attribute_modes.keys())

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(['SourceFile'] + attributes)
    for file_path, (date_time, file_tz) in file_date_map.items():
        row = [file_path]
        for attr in attributes:
            row.append(format_date_for_mode(date_time, attribute_modes[attr], file_tz))
        writer.writerow(row)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False,
                                     encoding='utf-8', newline='') as csv_tmp:
        csv_tmp.write(csv_buffer.getvalue())
        csv_path = csv_tmp.name

    # exiftool needs the source files listed on the command line (or via -@ filelist);
    # the -csv flag only supplies tag values, not the file list. Use a filelist to avoid
    # hitting the Windows command-line length limit.
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                     encoding='utf-8') as list_tmp:
        for file_path in file_date_map.keys():
            list_tmp.write(file_path + '\n')
        list_path = list_tmp.name

    try:
        result = subprocess.run(
            [EXIFTOOL, f'-csv={csv_path}', '-overwrite_original', '-@', list_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding='utf-8', errors='replace', timeout=WRITE_BATCH_TIMEOUT_SECONDS
        )
        if result.returncode != 0:
            msg = f"exiftool exit {result.returncode}: {(result.stderr or '')[:500]}"
            _logger.warning(msg)
            if error_log is not None:
                error_log.write(msg + "\n")
    except subprocess.TimeoutExpired:
        msg = f"Timeout during batch metadata write ({len(file_date_map)} files)"
        _logger.error(msg)
        if error_log is not None:
            error_log.write(msg + "\n")
    finally:
        os.unlink(csv_path)
        os.unlink(list_path)
