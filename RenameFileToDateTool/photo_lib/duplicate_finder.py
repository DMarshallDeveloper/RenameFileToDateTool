"""Find duplicate photos by content, group them, mark them with a suffix for review.

Three confidence tiers, each backed by a different hash:

  Tier 1 — ``file_sha256``: SHA-256 of the file's raw bytes. Two files match iff
    they're literally byte-for-byte identical. Useful for spotting accidental
    double-copies; useless for finding the same image saved at different quality.

  Tier 2 — ``pixel_sha256``: SHA-256 of the decoded RGB pixel bytes (resolution
    included in the digest). Catches photos whose EXIF differs but whose actual
    pixels are identical — exactly the case where one library copy got an EXIF
    rewrite the other didn't. Mathematically certain when it matches.

  Tier 3 — ``phash``: 64-bit perceptual hash (imagehash.phash). Same image at any
    quality / resolution / re-encoding produces close hashes; identical
    re-encodings produce equal hashes (Hamming distance 0). This module groups
    files with phash distance == 0 only — anything fuzzier should be reported,
    not auto-marked.

The ``_a/_b/_c`` suffix scheme: within each duplicate group, the file with the
best quality wins ``_a``. Others get ``_b``, ``_c``, ... in size-descending
order. Quality ranking:
  1. Pixel dimensions (width × height) — higher wins
  2. File size — bigger wins (less compression for the same dimensions)
  3. Filename — lexicographic, for deterministic tie-break

After the user manually deletes the duplicates they don't want, ``finalize`` strips
the ``_<letter>`` suffix off any survivor whose siblings have been removed —
returning lone files to the canonical ``YYYY-MM-DD HH.MM.SS_N.ext`` form.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import string
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from PIL import Image

from photo_lib.binaries import FFMPEG, FFPROBE
from photo_lib.extensions import is_image, is_video, normalize_extension
from photo_lib.filename_pattern import CANONICAL_FILENAME_PARTS_RE

logger = logging.getLogger("photo_lib")

# Pattern for the marked form: <canonical-base>_<idx>_<letter>.<ext>
MARKED_FILENAME_RE = re.compile(
    r'^(?P<base>\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2})'
    r'_(?P<idx>\d+)'
    r'_(?P<letter>[a-z])'
    r'\.(?P<ext>[a-zA-Z0-9]{3,4})$'
)


VIDEO_FRAME_COUNT = 5  # frames sampled per video for pHash comparison


@dataclass(frozen=True)
class FileFingerprint:
    """Everything we cache about a single media file, keyed by absolute path.

    Images carry ``pixel_sha256`` and ``phash_hex``; videos carry
    ``frame_phashes_hex`` (a tuple of pHashes from frames sampled at fixed time
    positions). The ``media_kind`` field distinguishes them so the grouper
    knows which fields to compare.
    """
    path: str
    size: int
    mtime: float
    media_kind: str  # "image" or "video"
    file_sha256: str
    pixel_sha256: str | None  # image only — None if PIL couldn't decode
    phash_hex: str | None     # image only
    frame_phashes_hex: tuple[str, ...] | None  # video only — None on decode failure
    width: int | None
    height: int | None

    @property
    def pixel_count(self) -> int:
        if self.width is None or self.height is None:
            return 0
        return self.width * self.height


@dataclass
class DuplicateGroup:
    """One cluster of files we believe are duplicates of the same image."""
    tier: int  # 1 = file-bytes, 2 = pixel-bytes, 3 = phash-zero
    fingerprints: list[FileFingerprint] = field(default_factory=list)

    def ranked(self) -> list[FileFingerprint]:
        """Files ordered best-to-worst by quality (winner first)."""
        return sorted(
            self.fingerprints,
            key=lambda fp: (-fp.pixel_count, -fp.size, fp.path),
        )


def hash_file_bytes(path: str, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of raw file bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def hash_image_pixels(path: str) -> tuple[str, int, int] | None:
    """SHA-256 of decoded RGB pixel bytes + the image's dimensions.

    Returns ``None`` if PIL can't open the file (corrupted / not an image).
    """
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            digest = hashlib.sha256()
            digest.update(f"{rgb.size}".encode("ascii"))
            digest.update(rgb.tobytes())
            return digest.hexdigest(), rgb.size[0], rgb.size[1]
    except Exception as exc:  # PIL raises a wide range of types; we don't care
        logger.debug("hash_image_pixels failed for %s: %s", path, exc)
        return None


def compute_phash_hex(path: str) -> str | None:
    """64-bit perceptual hash rendered as 16-char hex. None on decode failure."""
    try:
        import imagehash  # local import so non-imagehash codepaths don't pay
        with Image.open(path) as image:
            return str(imagehash.phash(image))
    except Exception as exc:
        logger.debug("compute_phash_hex failed for %s: %s", path, exc)
        return None


def get_video_duration_seconds(path: str) -> float | None:
    """Return the video's duration in seconds via ffprobe, or None on failure."""
    try:
        result = subprocess.run(
            [
                FFPROBE, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=duration", "-of", "json", path,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        streams = data.get("streams") or []
        if not streams:
            return None
        duration_str = streams[0].get("duration")
        if duration_str in (None, "N/A"):
            return None
        return float(duration_str)
    except (subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as exc:
        logger.debug("ffprobe duration failed for %s: %s", path, exc)
        return None


def compute_video_frame_phashes(path: str, frame_count: int = VIDEO_FRAME_COUNT) -> tuple[str, ...] | None:
    """Extract ``frame_count`` frames at evenly spaced positions and pHash each.

    Returns a tuple of hex pHash strings (length == frame_count), or None if
    ffprobe/ffmpeg/PIL fails. Two videos that produce equal tuples are very
    likely the same content even when their file bytes differ (transcoded).
    """
    duration = get_video_duration_seconds(path)
    if duration is None or duration <= 0:
        logger.debug("Skipping video phash for %s (no duration)", path)
        return None

    # Evenly spaced sample points strictly inside (0, duration). For 5 frames
    # that's 1/6, 2/6, ... 5/6 of the way through.
    sample_seconds = [duration * (n + 1) / (frame_count + 1) for n in range(frame_count)]

    try:
        import imagehash
    except ImportError:
        logger.warning("imagehash not installed; video phash unavailable.")
        return None

    hashes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="video_phash_") as tmpdir:
        for index, second_offset in enumerate(sample_seconds):
            frame_path = os.path.join(tmpdir, f"frame_{index:02d}.jpg")
            result = subprocess.run(
                [
                    FFMPEG, "-y", "-ss", f"{second_offset:.3f}", "-i", path,
                    "-frames:v", "1", "-vf", "scale=320:-1",
                    "-loglevel", "error", frame_path,
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                encoding="utf-8", errors="replace", timeout=30,
            )
            if result.returncode != 0 or not os.path.exists(frame_path):
                logger.debug("ffmpeg frame extract failed for %s @ %.2fs: %s",
                             path, second_offset, result.stderr.strip()[:200])
                return None
            try:
                with Image.open(frame_path) as image:
                    hashes.append(str(imagehash.phash(image)))
            except Exception as exc:
                logger.debug("phash of extracted frame failed for %s: %s", path, exc)
                return None
    return tuple(hashes)


def fingerprint_file(path: str) -> FileFingerprint | None:
    """Build a fingerprint for an image or video file. ``None`` for other types."""
    extension = normalize_extension(os.path.splitext(path)[1])
    stat_result = os.stat(path)
    if is_image(extension):
        file_sha = hash_file_bytes(path)
        pixel_result = hash_image_pixels(path)
        phash_hex = compute_phash_hex(path)
        if pixel_result is None:
            pixel_sha, width, height = None, None, None
        else:
            pixel_sha, width, height = pixel_result
        return FileFingerprint(
            path=path, size=stat_result.st_size, mtime=stat_result.st_mtime,
            media_kind="image",
            file_sha256=file_sha,
            pixel_sha256=pixel_sha, phash_hex=phash_hex,
            frame_phashes_hex=None,
            width=width, height=height,
        )
    if is_video(extension):
        file_sha = hash_file_bytes(path)
        frame_phashes = compute_video_frame_phashes(path)
        return FileFingerprint(
            path=path, size=stat_result.st_size, mtime=stat_result.st_mtime,
            media_kind="video",
            file_sha256=file_sha,
            pixel_sha256=None, phash_hex=None,
            frame_phashes_hex=frame_phashes,
            width=None, height=None,
        )
    return None


def group_duplicates(fingerprints: Iterable[FileFingerprint]) -> list[DuplicateGroup]:
    """Cluster fingerprints into duplicate groups using tiers 1-3.

    A file is placed into the FIRST tier it joins a multi-member group in: a
    pair of byte-identical files goes into tier 1 only, not tier 1 *and* tier 2.
    This avoids reporting the same pair under multiple tiers.
    """
    by_file_sha: dict[str, list[FileFingerprint]] = defaultdict(list)
    by_pixel_sha: dict[str, list[FileFingerprint]] = defaultdict(list)
    by_phash: dict[str, list[FileFingerprint]] = defaultdict(list)
    by_frame_phashes: dict[tuple[str, ...], list[FileFingerprint]] = defaultdict(list)

    materialized = list(fingerprints)
    for fingerprint in materialized:
        by_file_sha[fingerprint.file_sha256].append(fingerprint)
        if fingerprint.pixel_sha256 is not None:
            by_pixel_sha[fingerprint.pixel_sha256].append(fingerprint)
        if fingerprint.phash_hex is not None:
            by_phash[fingerprint.phash_hex].append(fingerprint)
        if fingerprint.frame_phashes_hex is not None:
            by_frame_phashes[fingerprint.frame_phashes_hex].append(fingerprint)

    used_paths: set[str] = set()
    groups: list[DuplicateGroup] = []

    def _emit(tier: int, members: list[FileFingerprint]) -> None:
        free_members = [m for m in members if m.path not in used_paths]
        if len(free_members) < 2:
            return
        for member in free_members:
            used_paths.add(member.path)
        groups.append(DuplicateGroup(tier=tier, fingerprints=free_members))

    # Tier 1: byte-identical files (any media kind).
    for members in by_file_sha.values():
        _emit(1, members)
    # Tier 2: identical decoded pixels (images) OR identical frame-pHash tuple
    # (videos). Different signals, same "essentially the same content with
    # different EXIF/metadata" semantic — kept in one tier so the report shows
    # them with the same confidence badge.
    for members in by_pixel_sha.values():
        _emit(2, members)
    for members in by_frame_phashes.values():
        _emit(2, members)
    # Tier 3: same image-pHash (covers re-encoded / resized images).
    for members in by_phash.values():
        _emit(3, members)

    return groups


def next_marked_path(fingerprint: FileFingerprint, letter: str) -> str:
    """Build the ``<base>_<idx>_<letter>.<ext>`` form for a fingerprint, or
    raise ValueError if the file's name isn't canonical."""
    base_name = os.path.basename(fingerprint.path)
    match = CANONICAL_FILENAME_PARTS_RE.match(base_name)
    if match is None:
        raise ValueError(f"Not a canonical filename: {base_name}")
    base = match.group("base")
    idx = match.group("idx")
    ext = match.group("ext")
    new_name = f"{base}_{idx}_{letter}.{ext}"
    return os.path.join(os.path.dirname(fingerprint.path), new_name)


def plan_mark(groups: Iterable[DuplicateGroup]) -> list[tuple[str, str, int]]:
    """Return ``[(old_path, new_path, tier), ...]`` for renaming files into _a/_b/_c form.

    The winner of each group gets ``_a``, runners-up get ``_b``, ``_c``, ... in
    quality order. Non-canonical filenames in a group are skipped (logged).
    """
    plan: list[tuple[str, str, int]] = []
    for group in groups:
        ranked = group.ranked()
        if len(ranked) > len(string.ascii_lowercase):
            logger.warning("Group of %d duplicates exceeds 26-letter suffix range; "
                           "skipping. First member: %s",
                           len(ranked), ranked[0].path)
            continue
        for letter, fingerprint in zip(string.ascii_lowercase, ranked):
            try:
                new_path = next_marked_path(fingerprint, letter)
            except ValueError as exc:
                logger.warning("plan_mark skipping non-canonical %s: %s",
                               fingerprint.path, exc)
                continue
            if new_path != fingerprint.path:
                plan.append((fingerprint.path, new_path, group.tier))
    return plan


def plan_finalize(root: str) -> list[tuple[str, str]]:
    """After manual review, find ``<base>_<idx>_<letter>`` files whose siblings
    are gone, and propose stripping the ``_<letter>`` suffix.

    A 'sibling' is any file with the same ``<base>_<idx>_<other-letter>.<ext>``
    where ``other-letter != letter``. If none exist for a given file, it's a
    'lone survivor' and gets demoted back to ``<base>_<idx>.<ext>``.
    """
    plan: list[tuple[str, str]] = []
    for current_dir, _subdirs, filenames in os.walk(root):
        marked = {}  # (base, idx) -> {letter: filename}
        for name in filenames:
            match = MARKED_FILENAME_RE.match(name)
            if not match:
                continue
            key = (match.group("base"), match.group("idx"))
            marked.setdefault(key, []).append((match.group("letter"), name, match.group("ext")))
        for (base, idx), entries in marked.items():
            if len(entries) > 1:
                # Still has siblings — leave them all alone.
                continue
            letter, name, ext = entries[0]
            old_path = os.path.join(current_dir, name)
            new_name = f"{base}_{idx}.{ext}"
            new_path = os.path.join(current_dir, new_name)
            if os.path.exists(new_path):
                # A canonical-name file already exists at the target; don't clobber it.
                logger.warning(
                    "finalize: target exists, skipping %s -> %s", name, new_name
                )
                continue
            plan.append((old_path, new_path))
    return plan


def apply_simple_rename_plan(plan: list[tuple[str, str]]) -> int:
    """Two-phase staged rename (mirrors photo_lib.canonical_renumber)."""
    if not plan:
        return 0
    staged: list[tuple[str, str]] = []
    for old_path, new_path in plan:
        temp_path = old_path + ".__renaming__"
        os.rename(old_path, temp_path)
        staged.append((temp_path, new_path))
    for temp_path, new_path in staged:
        os.rename(temp_path, new_path)
    return len(staged)
