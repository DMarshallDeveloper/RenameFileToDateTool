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
from photo_lib.config import BUNDLED_EARLY_FOLDER, BUNDLED_EARLY_YEAR_RANGE
from photo_lib.extensions import is_image, is_video, normalize_extension
from photo_lib.filename_pattern import CANONICAL_FILENAME_PARTS_RE

BUNDLED_EARLY_YEARS = set(range(*BUNDLED_EARLY_YEAR_RANGE))

logger = logging.getLogger("photo_lib")

# Pattern for the marked form, with two optional decorations:
#   plain:                          <winner_base>_<idx>_<letter>.<ext>
#   + source:                       <winner_base>_<idx>_<letter>__src_<label>.<ext>
#   + origin (cross-date):          <winner_base>_<idx>_<letter>__from_<loser_base>.<ext>
#   + source + origin:              <winner_base>_<idx>_<letter>__src_<label>__from_<loser_base>.<ext>
#
# ``__src_<label>`` carries provenance: which source library this file came
# from (master vs takeout vs USB). The label is sanitized to
# ``[A-Za-z0-9-]+`` at combine time so the marker terminates unambiguously
# before the next ``__`` delimiter or the extension dot.
#
# ``__from_<loser_base>`` carries the loser's original timestamp when the
# duplicate group spanned different dates — without this marker the loser's
# date would be erased by the rename. Finalize uses ``origin_base`` to send
# a surviving cross-date loser back to its original year folder.
#
# Both markers drop off at finalize: they exist only during the review window.
MARKED_FILENAME_RE = re.compile(
    r'^(?P<base>\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2})'
    r'_(?P<idx>\d+)'
    r'_(?P<letter>[a-z])'
    r'(?:__src_(?P<src>[A-Za-z0-9-]+))?'
    r'(?:__from_(?P<origin_base>\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2}))?'
    r'\.(?P<ext>[a-zA-Z0-9]{3,4})$'
)


VIDEO_FRAME_COUNT = 5  # frames sampled per video for pHash comparison

# Default Hamming-distance threshold for fuzzy pHash matching in tier 3.
# 0  = exact match only (strict). Misses takeout-re-encoded copies whose pHash
#      shifts by a few bits.
# 8  = covers same-image-re-saved-at-same-quality (~4 bits) and same-image-
#      re-saved-at-lower-quality (~10-12 bits) with a small safety margin.
# 16 = aggressive; starts catching visually-similar-but-different shots.
# A pHash is 64 bits so any threshold > ~30 collapses everything.
DEFAULT_PHASH_HAMMING_THRESHOLD = 8

# A pHash with very few set bits (or very few cleared bits) is "low entropy"
# and meaningless for fuzzy matching — e.g., a solid-color thumbnail hashes to
# all-zeros and would falsely cluster with every other solid-color thumbnail.
# Require at least this many bits in each direction to participate in tier 3.
_MIN_PHASH_BITS_EACH_WAY = 8


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


def hamming_distance_hex(hex_a: str, hex_b: str) -> int:
    """Hamming distance between two equal-length hex pHash strings (bit-wise)."""
    return (int(hex_a, 16) ^ int(hex_b, 16)).bit_count()


def is_low_entropy_phash(phash_hex: str) -> bool:
    """True iff the pHash has too few set/cleared bits to be useful for fuzzy
    matching — protects against solid-color thumbnails forming a giant fake
    cluster at any non-zero threshold."""
    bits_set = int(phash_hex, 16).bit_count()
    return bits_set < _MIN_PHASH_BITS_EACH_WAY or bits_set > 64 - _MIN_PHASH_BITS_EACH_WAY


def _union_find_cluster(items: list, edge_predicate) -> list[list]:
    """Group ``items`` into clusters where ``edge_predicate(a, b)`` holds.

    Quadratic in len(items); fine for libraries up to ~50k since each pair-test
    is just XOR + bit_count. Returns only clusters of size ≥ 2.
    """
    n = len(items)
    parent = list(range(n))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]  # path-halving
            node = parent[node]
        return node

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    for i in range(n):
        for j in range(i + 1, n):
            if edge_predicate(items[i], items[j]):
                union(i, j)

    clusters: dict[int, list] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(items[i])
    return [members for members in clusters.values() if len(members) >= 2]


def group_duplicates(
    fingerprints: Iterable[FileFingerprint],
    phash_hamming_threshold: int = 0,
) -> list[DuplicateGroup]:
    """Cluster fingerprints into duplicate groups using tiers 1-3.

    A file is placed into the FIRST tier it joins a multi-member group in: a
    pair of byte-identical files goes into tier 1 only, not tier 1 *and* tier 2.
    This avoids reporting the same pair under multiple tiers.

    ``phash_hamming_threshold`` controls tier 3 (image pHash) and the
    video-frame tier 2 fuzzy match:
      - 0 = exact equality only; fastest, strictest. Same as the original
        behavior.
      - > 0 = a pair counts as a match if Hamming distance ≤ threshold.
        Catches takeout re-encoding (pHashes typically differ by 4-12 bits).
        Solid-color / low-entropy pHashes are excluded from fuzzy matching to
        prevent giant false clusters.
    """
    by_file_sha: dict[str, list[FileFingerprint]] = defaultdict(list)
    by_pixel_sha: dict[str, list[FileFingerprint]] = defaultdict(list)
    by_phash_exact: dict[str, list[FileFingerprint]] = defaultdict(list)
    by_frame_phashes_exact: dict[tuple[str, ...], list[FileFingerprint]] = defaultdict(list)

    materialized = list(fingerprints)
    for fingerprint in materialized:
        by_file_sha[fingerprint.file_sha256].append(fingerprint)
        if fingerprint.pixel_sha256 is not None:
            by_pixel_sha[fingerprint.pixel_sha256].append(fingerprint)
        if fingerprint.phash_hex is not None:
            by_phash_exact[fingerprint.phash_hex].append(fingerprint)
        if fingerprint.frame_phashes_hex is not None:
            by_frame_phashes_exact[fingerprint.frame_phashes_hex].append(fingerprint)

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
    for members in by_frame_phashes_exact.values():
        _emit(2, members)

    # Tier 3 (images): pHash match. Exact-equality buckets first (cheap);
    # fuzzy union-find cluster only for fingerprints not already claimed.
    for members in by_phash_exact.values():
        _emit(3, members)

    if phash_hamming_threshold > 0:
        # Fuzzy tier 3 (images): cluster the remaining pHash-bearing fingerprints
        # by Hamming distance, excluding low-entropy hashes (solid colors).
        image_remainders = [
            fp for fp in materialized
            if fp.phash_hex is not None
            and fp.path not in used_paths
            and not is_low_entropy_phash(fp.phash_hex)
        ]
        if image_remainders:
            for cluster in _union_find_cluster(
                image_remainders,
                lambda a, b: hamming_distance_hex(a.phash_hex, b.phash_hex)
                              <= phash_hamming_threshold,
            ):
                _emit(3, cluster)

        # Fuzzy tier 2 (videos): two videos match iff EVERY corresponding
        # frame is within threshold. Stricter than image tier 3 because the
        # signal is per-frame; one stray frame mismatch likely means different
        # clips.
        video_remainders = [
            fp for fp in materialized
            if fp.frame_phashes_hex is not None
            and fp.path not in used_paths
        ]
        if video_remainders:
            def _videos_fuzzy_match(a: FileFingerprint, b: FileFingerprint) -> bool:
                if len(a.frame_phashes_hex) != len(b.frame_phashes_hex):
                    return False
                return all(
                    hamming_distance_hex(frame_a, frame_b) <= phash_hamming_threshold
                    for frame_a, frame_b in zip(a.frame_phashes_hex, b.frame_phashes_hex)
                )
            for cluster in _union_find_cluster(video_remainders, _videos_fuzzy_match):
                _emit(2, cluster)

    return groups


def _parse_canonical_parts(path: str) -> tuple[str, str, str] | None:
    """Return ``(base, idx, ext)`` for a canonical filename, or None if it's
    not canonical."""
    basename = os.path.basename(path)
    match = CANONICAL_FILENAME_PARTS_RE.match(basename)
    if match is None:
        return None
    return match.group("base"), match.group("idx"), match.group("ext")


def plan_mark(
    groups: Iterable[DuplicateGroup],
    source_label_lookup: dict[str, str] | None = None,
) -> list[tuple[str, str, int]]:
    """Return ``[(old_path, new_path, tier), ...]`` for renaming files into _a/_b/_c form.

    All files in a duplicate group share the WINNER's ``<base>_<idx>`` prefix
    and end up in the WINNER's folder — that's what makes the marked files
    sort adjacent when scrolling in File Explorer:

      group winner:   2014-01-01 13.00.00_1.jpg   -> 2014-01-01 13.00.00_1_a.jpg
      group dup #2:   2014-01-01 13.00.00_14.jpg  -> 2014-01-01 13.00.00_1_b.jpg
      group dup #3:   2014-01-01 13.00.00_29.jpg  -> 2014-01-01 13.00.00_1_c.jpg

    Each file keeps its own extension (so .heic, .mp4, .mov pairs at the same
    timestamp stay distinguishable within a group).

    **Source labels** (optional): pass ``source_label_lookup`` mapping each
    fingerprint's path to a sanitized source label (from the combine manifest).
    Every marked file then carries ``__src_<label>`` so the user can see at a
    glance which source library each duplicate came from. Paths missing from
    the lookup don't get a marker — the function tolerates partial coverage.

    **Cross-date groups** (members with different timestamps): each loser
    carries a ``__from_<loser_base>`` marker so its original date stays
    visible in the filename and ``plan_finalize`` can send the file back home
    if the user decides it wasn't really a duplicate.

    With both decorations present the form is::

      <winner_base>_<idx>_<letter>__src_<label>__from_<loser_base>.<ext>

    If the WINNER's filename isn't canonical, the entire group is skipped —
    we have no canonical prefix to share. If a non-winner is non-canonical,
    only that file is dropped from the plan.
    """
    lookup = source_label_lookup or {}
    plan: list[tuple[str, str, int]] = []
    for group in groups:
        ranked = group.ranked()
        if len(ranked) > len(string.ascii_lowercase):
            logger.warning("Group of %d duplicates exceeds 26-letter suffix range; "
                           "skipping. First member: %s",
                           len(ranked), ranked[0].path)
            continue
        winner_parts = _parse_canonical_parts(ranked[0].path)
        if winner_parts is None:
            logger.warning("plan_mark skipping group: winner has non-canonical name %s",
                           ranked[0].path)
            continue
        winner_base, winner_idx, _winner_ext = winner_parts
        winner_dir = os.path.dirname(ranked[0].path)
        for letter, fingerprint in zip(string.ascii_lowercase, ranked):
            file_parts = _parse_canonical_parts(fingerprint.path)
            if file_parts is None:
                logger.warning("plan_mark skipping non-canonical member %s",
                               fingerprint.path)
                continue
            loser_base, _loser_idx, file_ext = file_parts
            src_label = lookup.get(os.path.normpath(fingerprint.path))
            src_marker = f"__src_{src_label}" if src_label else ""
            if loser_base == winner_base:
                new_name = (
                    f"{winner_base}_{winner_idx}_{letter}{src_marker}.{file_ext}"
                )
            else:
                new_name = (
                    f"{winner_base}_{winner_idx}_{letter}{src_marker}"
                    f"__from_{loser_base}.{file_ext}"
                )
            new_path = os.path.join(winner_dir, new_name)
            if new_path != fingerprint.path:
                plan.append((fingerprint.path, new_path, group.tier))
    return plan


def _detect_year_folder_convention(root: str) -> bool:
    """True iff ``root`` contains at least one year-shaped subfolder.

    A year-shaped subfolder is one named with a 4-digit year (e.g. ``2014``)
    or matching ``BUNDLED_EARLY_FOLDER`` ("2000 - 2010"). When the root has
    none of these, we treat the library as flat — cross-date losers in
    finalize stay at the root instead of synthesizing a year subfolder
    where none existed (which would silently restructure the user's library).
    """
    if not os.path.isdir(root):
        return False
    for name in os.listdir(root):
        if not os.path.isdir(os.path.join(root, name)):
            continue
        if name == BUNDLED_EARLY_FOLDER:
            return True
        if len(name) == 4 and name.isdigit():
            year = int(name)
            if 1900 <= year <= 2100:
                return True
    return False


def _target_folder_for_base(root: str, base: str, *,
                            use_year_folders: bool) -> str:
    """Destination folder under ``root`` for a canonical timestamp base.

    With ``use_year_folders=True`` (a library that already has year
    subfolders) this mirrors ``ingest_inbox_to_master.target_folder_for_year``:
    years in the bundled-early range collapse into ``BUNDLED_EARLY_FOLDER``;
    everything else lives in ``<year>``.

    With ``use_year_folders=False`` (a flat library) the root itself is the
    destination — finalize won't introduce year subfolders into a library
    that wasn't already using them.
    """
    if not use_year_folders:
        return root
    year = int(base[:4])
    if year in BUNDLED_EARLY_YEARS:
        return os.path.join(root, BUNDLED_EARLY_FOLDER)
    return os.path.join(root, str(year))


def plan_finalize(root: str) -> list[tuple[str, str]]:
    """After manual review, strip the ``_<letter>`` (and any ``__from_<base>``)
    suffix from marked files, returning each to a canonical name.

    Three cases:

    - **Lone survivor**, no origin marker (1 letter remaining in the group):
      the file gets demoted back to ``<base>_<idx>.<ext>``. This is the
      common case — the user kept the winning ``_a`` copy and deleted the rest.

    - **Multi-survivor**, no origin marker (2+ letters remain): the user
      decided some of the group's members aren't actually duplicates. The
      first surviving letter (alphabetically — usually ``_a``, the winner)
      takes the group's original ``<idx>``; subsequent survivors are
      assigned the next free indices in that timestamp bucket
      (``<idx>+1``, ``<idx>+2``, … bumping past any canonical file already
      there).

    - **Surviving cross-date loser** (``__from_<loser_base>`` marker on a
      ``_b``/``_c``/… that the user kept): the file is moved back into its
      original year folder and renamed to ``<loser_base>_<next-free-idx>.<ext>``.
      A surviving ``_a`` never carries the marker by construction (the winner
      defines the group's date).

    The whole tree is walked once before allocation so the two-pass order
    (all position-0 entries across all groups before any position-1 entries)
    spans folders — necessary because a returning cross-date ``_b`` can
    compete with a same-base ``_b`` overflow for indices in a shared
    destination bucket.

    Year-folder routing for returning cross-date losers respects the
    convention already in use under ``root``: if year subfolders exist the
    returning file goes to its appropriate year folder; if the library is
    flat the file stays at ``root``.
    """
    use_year_folders = _detect_year_folder_convention(root)

    # Phase 1: walk the tree once; collect marked entries and per-folder
    # per-base canonical indices that are already taken.
    marked_entries: list[dict] = []
    used_canonical_by_dir_base: dict[tuple[str, str], set[int]] = {}

    for current_dir, _subdirs, filenames in os.walk(root):
        for name in filenames:
            marked_match = MARKED_FILENAME_RE.match(name)
            if marked_match:
                marked_entries.append({
                    "folder": current_dir,
                    "winner_base": marked_match.group("base"),
                    "winner_idx": int(marked_match.group("idx")),
                    "letter": marked_match.group("letter"),
                    "origin_base": marked_match.group("origin_base"),
                    "name": name,
                    "ext": marked_match.group("ext"),
                })
                continue
            canonical_match = CANONICAL_FILENAME_PARTS_RE.match(name)
            if canonical_match:
                used_canonical_by_dir_base.setdefault(
                    (current_dir, canonical_match.group("base")), set()
                ).add(int(canonical_match.group("idx")))

    # Phase 2: bucket marked entries by their winner group; sort by letter.
    marked_groups: dict[tuple[str, str, int], list[dict]] = {}
    for entry in marked_entries:
        key = (entry["folder"], entry["winner_base"], entry["winner_idx"])
        marked_groups.setdefault(key, []).append(entry)
    for entries in marked_groups.values():
        entries.sort(key=lambda e: e["letter"])

    # Phase 3: two-pass allocation across all folders.
    plan: list[tuple[str, str]] = []
    sorted_group_keys = sorted(marked_groups.keys())
    max_letters = max((len(items) for items in marked_groups.values()), default=0)

    for position in range(max_letters):
        for group_key in sorted_group_keys:
            entries = marked_groups[group_key]
            if position >= len(entries):
                continue
            entry = entries[position]
            origin_base = entry["origin_base"]

            if origin_base is None:
                # Same-base mark: stay in current folder. Position 0 gets
                # winner_idx, position 1 gets winner_idx + 1, …
                target_dir = entry["folder"]
                target_base = entry["winner_base"]
                preferred_idx = entry["winner_idx"] + position
            else:
                # Cross-date loser: move back to origin year's folder. The
                # winner_idx context has no meaning in the destination
                # bucket — allocate the lowest free idx instead.
                target_dir = _target_folder_for_base(
                    root, origin_base, use_year_folders=use_year_folders,
                )
                target_base = origin_base
                preferred_idx = 1

            bucket = used_canonical_by_dir_base.setdefault(
                (target_dir, target_base), set()
            )
            while preferred_idx in bucket:
                preferred_idx += 1
            bucket.add(preferred_idx)

            new_name = f"{target_base}_{preferred_idx}.{entry['ext']}"
            old_path = os.path.join(entry["folder"], entry["name"])
            new_path = os.path.join(target_dir, new_name)
            if new_path == old_path:
                continue
            if os.path.exists(new_path):
                # Tracked state says the slot is free but disk disagrees —
                # likely a non-canonical filename we didn't index. Safer
                # to skip than clobber.
                logger.warning(
                    "finalize: target exists, skipping %s -> %s",
                    entry["name"], new_name,
                )
                continue
            plan.append((old_path, new_path))
    return plan


def apply_simple_rename_plan(plan: list[tuple[str, str]]) -> int:
    """Two-phase staged rename (mirrors photo_lib.canonical_renumber).

    Tolerates cross-folder destinations: the target directory is created on
    demand before the staged file is rolled into its final name.
    """
    if not plan:
        return 0
    staged: list[tuple[str, str]] = []
    for old_path, new_path in plan:
        temp_path = old_path + ".__renaming__"
        os.rename(old_path, temp_path)
        staged.append((temp_path, new_path))
    for temp_path, new_path in staged:
        target_dir = os.path.dirname(new_path)
        if target_dir and not os.path.isdir(target_dir):
            os.makedirs(target_dir, exist_ok=True)
        os.rename(temp_path, new_path)
    return len(staged)
