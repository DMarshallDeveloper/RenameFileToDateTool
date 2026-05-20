"""Sidecar SQLite cache for FileFingerprint records.

A library scan over 30k+ images takes ~30 minutes (PIL decode + pHash dominate).
This cache stores the fingerprint of each file keyed by ``(path, size, mtime)``
so re-runs touch only files that have changed. Deleting the cache file forces a
full re-scan.

The cache file lives at ``<library_root>/.photo_hashes.db`` by default — sidecar
to the library it describes, so it's easy to ship around or wipe.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing

from photo_lib.duplicate_finder import FileFingerprint


DEFAULT_CACHE_FILENAME = ".photo_hashes.db"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fingerprints (
    path                TEXT PRIMARY KEY,
    size                INTEGER NOT NULL,
    mtime               REAL NOT NULL,
    media_kind          TEXT NOT NULL DEFAULT 'image',
    file_sha256         TEXT NOT NULL,
    pixel_sha256        TEXT,
    phash_hex           TEXT,
    frame_phashes_json  TEXT,
    width               INTEGER,
    height              INTEGER
);
"""


class FingerprintCache:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._connection = sqlite3.connect(db_path)
        with closing(self._connection.cursor()) as cursor:
            cursor.executescript(SCHEMA_SQL)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "FingerprintCache":
        return self

    def __exit__(self, *exception_info) -> None:
        self.close()

    _SELECT_COLUMNS = (
        "path, size, mtime, media_kind, file_sha256, pixel_sha256, phash_hex, "
        "frame_phashes_json, width, height"
    )

    @staticmethod
    def _row_to_fingerprint(row) -> FileFingerprint:
        frame_phashes_json = row[7]
        frame_phashes = (
            tuple(json.loads(frame_phashes_json))
            if frame_phashes_json else None
        )
        return FileFingerprint(
            path=row[0], size=row[1], mtime=row[2],
            media_kind=row[3] or "image",
            file_sha256=row[4],
            pixel_sha256=row[5], phash_hex=row[6],
            frame_phashes_hex=frame_phashes,
            width=row[8], height=row[9],
        )

    def lookup(self, path: str, size: int, mtime: float) -> FileFingerprint | None:
        """Return a cached fingerprint iff its stored size+mtime match the
        current file (so stale entries don't get returned)."""
        with closing(self._connection.cursor()) as cursor:
            cursor.execute(
                f"SELECT {self._SELECT_COLUMNS} FROM fingerprints WHERE path = ?",
                (path,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        # Tolerate sub-second mtime drift across filesystems by rounding.
        if row[1] != size or round(row[2], 3) != round(mtime, 3):
            return None
        return self._row_to_fingerprint(row)

    def store(self, fingerprint: FileFingerprint) -> None:
        frame_phashes_json = (
            json.dumps(list(fingerprint.frame_phashes_hex))
            if fingerprint.frame_phashes_hex is not None else None
        )
        with closing(self._connection.cursor()) as cursor:
            cursor.execute(
                "INSERT OR REPLACE INTO fingerprints "
                "(path, size, mtime, media_kind, file_sha256, pixel_sha256, "
                " phash_hex, frame_phashes_json, width, height) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fingerprint.path,
                    fingerprint.size,
                    fingerprint.mtime,
                    fingerprint.media_kind,
                    fingerprint.file_sha256,
                    fingerprint.pixel_sha256,
                    fingerprint.phash_hex,
                    frame_phashes_json,
                    fingerprint.width,
                    fingerprint.height,
                ),
            )
        self._connection.commit()

    def all_fingerprints(self) -> list[FileFingerprint]:
        """Read every cached fingerprint. Useful when you want to dedup based on
        a previous scan without re-hashing anything."""
        with closing(self._connection.cursor()) as cursor:
            cursor.execute(f"SELECT {self._SELECT_COLUMNS} FROM fingerprints")
            rows = cursor.fetchall()
        return [self._row_to_fingerprint(row) for row in rows]

    def forget(self, path: str) -> None:
        with closing(self._connection.cursor()) as cursor:
            cursor.execute("DELETE FROM fingerprints WHERE path = ?", (path,))
        self._connection.commit()

    def rename(self, old_path: str, new_path: str) -> bool:
        """Update the path key of a cached fingerprint after the file moved.

        Lets a ``mark``/``finalize`` pass shuffle filenames without invalidating
        the expensive hash data — a subsequent ``report`` can then read the
        cache directly and produce a refreshed view with the new filenames,
        no re-scan needed.

        Returns True iff an entry was actually moved (False if old_path wasn't
        in the cache, or if new_path was already occupied — in which case the
        caller probably has a worse bug).
        """
        with closing(self._connection.cursor()) as cursor:
            cursor.execute(
                "UPDATE fingerprints SET path = ? WHERE path = ?",
                (new_path, old_path),
            )
            moved = cursor.rowcount > 0
        self._connection.commit()
        return moved


def default_cache_path(library_root: str) -> str:
    return os.path.join(library_root, DEFAULT_CACHE_FILENAME)
