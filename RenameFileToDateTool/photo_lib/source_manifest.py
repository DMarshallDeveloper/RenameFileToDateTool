"""Sidecar SQLite manifest mapping ``dest_path -> source_label``.

``combine_libraries`` writes this file when copying multi-source libraries
into one tree. Each row records which source library a given destination
file came from, so downstream tools can:

  * stamp ``__src_<label>`` onto marked filenames during ``find_duplicate_photos
    mark`` (preserves provenance across the rename to ``_a/_b/_c``)
  * surface the source label per card in the duplicate-review HTML
  * roll up counts and sizes per source in the stats HTML

Lives at ``<library_root>/.source_manifest.db`` by default. Path keys are
canonicalised the same way ``duplicate_cache`` does so spelling variants of
the same path don't double up.
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing


DEFAULT_MANIFEST_FILENAME = ".source_manifest.db"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS source_labels (
    path          TEXT PRIMARY KEY,
    source_label  TEXT NOT NULL
);
"""


def _canonical_key(path: str) -> str:
    return os.path.normpath(path)


def default_manifest_path(library_root: str) -> str:
    return os.path.normpath(os.path.join(library_root, DEFAULT_MANIFEST_FILENAME))


def sanitize_source_label(raw: str) -> str:
    """Make ``raw`` safe to embed in a ``__src_<label>__`` filename marker.

    Replaces any run of non-alphanumeric chars with a single hyphen and trims
    leading/trailing hyphens. The marker regex consumes ``[A-Za-z0-9-]+`` so
    keeping the label inside that charset is what lets ``__src_`` /
    ``__from_`` neighbouring markers parse unambiguously — an underscore in
    the label would collide with the marker delimiters.
    """
    return re.sub(r'[^A-Za-z0-9]+', '-', raw).strip('-')


_YEAR_FOLDER_RE = re.compile(r'^\d{4}$')
_YEAR_RANGE_FOLDER_RE = re.compile(r'^\d{4} - \d{4}$')


def derive_source_label(source_root: str) -> str:
    """Pick a label for ``source_root`` automatically.

    Default: the basename of the source path. If the basename looks like a
    bare year folder (``2014`` or ``2000 - 2010``) we step up to the parent
    folder, since otherwise a pilot run like
    ``--source D:\\Photos\\2014 --source F:\\Backup\\2014`` would produce
    identical labels for two distinct sources.
    """
    canonical = os.path.normpath(source_root).rstrip(os.sep)
    basename = os.path.basename(canonical)
    if _YEAR_FOLDER_RE.fullmatch(basename) or _YEAR_RANGE_FOLDER_RE.fullmatch(basename):
        parent = os.path.basename(os.path.dirname(canonical))
        if parent:
            basename = parent
    return sanitize_source_label(basename)


class SourceManifest:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._connection = sqlite3.connect(db_path)
        with closing(self._connection.cursor()) as cursor:
            cursor.executescript(SCHEMA_SQL)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SourceManifest":
        return self

    def __exit__(self, *exception_info) -> None:
        self.close()

    def set(self, path: str, source_label: str) -> None:
        with closing(self._connection.cursor()) as cursor:
            cursor.execute(
                "INSERT OR REPLACE INTO source_labels (path, source_label) "
                "VALUES (?, ?)",
                (_canonical_key(path), source_label),
            )
        self._connection.commit()

    def lookup(self, path: str) -> str | None:
        with closing(self._connection.cursor()) as cursor:
            cursor.execute(
                "SELECT source_label FROM source_labels WHERE path = ?",
                (_canonical_key(path),),
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def rename(self, old_path: str, new_path: str) -> bool:
        """Update the path key after a normalize/mark/finalize rename.

        Returns True iff a row was actually moved. The manifest is loose with
        old_path being missing — callers may not know which files have entries
        — but blows up if new_path already has one, since that would silently
        overwrite a real entry.
        """
        old_key = _canonical_key(old_path)
        new_key = _canonical_key(new_path)
        with closing(self._connection.cursor()) as cursor:
            cursor.execute(
                "UPDATE source_labels SET path = ? WHERE path = ?",
                (new_key, old_key),
            )
            moved = cursor.rowcount > 0
        self._connection.commit()
        return moved

    def forget(self, path: str) -> None:
        with closing(self._connection.cursor()) as cursor:
            cursor.execute(
                "DELETE FROM source_labels WHERE path = ?",
                (_canonical_key(path),),
            )
        self._connection.commit()

    _RENAMING_SUFFIX = ".__renaming__"

    def rename_many(self, pairs: list[tuple[str, str]]) -> int:
        """Apply many path renames as a two-phase update, mirroring how
        ``canonical_renumber.apply_rename_plan`` stages on-disk renames.

        Phase 1 routes every old path to a unique temp key (old + suffix).
        Phase 2 routes the temp keys to their final destinations. This avoids
        a UNIQUE-constraint failure when one plan entry's new path equals
        another (still-to-be-renamed) entry's old path — exactly the situation
        that arises during a bucket renumber where ``_2.MOV → _4.mov`` and
        the manifest already has a row at ``_4.mov`` belonging to a different
        source file that will itself be renumbered later in the plan.

        Returns the number of rows updated in phase 2 (i.e. the number of
        renames that actually had a manifest row to migrate; rows missing
        from phase 1 are silently skipped).
        """
        if not pairs:
            return 0
        with closing(self._connection.cursor()) as cursor:
            for old_path, _new_path in pairs:
                old_key = _canonical_key(old_path)
                temp_key = old_key + self._RENAMING_SUFFIX
                cursor.execute(
                    "UPDATE source_labels SET path = ? WHERE path = ?",
                    (temp_key, old_key),
                )
            moved = 0
            for old_path, new_path in pairs:
                temp_key = _canonical_key(old_path) + self._RENAMING_SUFFIX
                new_key = _canonical_key(new_path)
                cursor.execute(
                    "UPDATE source_labels SET path = ? WHERE path = ?",
                    (new_key, temp_key),
                )
                moved += cursor.rowcount
        self._connection.commit()
        return moved

    def all_entries(self) -> dict[str, str]:
        with closing(self._connection.cursor()) as cursor:
            cursor.execute("SELECT path, source_label FROM source_labels")
            return {row[0]: row[1] for row in cursor.fetchall()}
