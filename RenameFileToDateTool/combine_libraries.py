"""combine_libraries.py — copy files from one or more source libraries into a single dest.

Walks each source recursively, copying every file into the matching subfolder
inside the destination. When two sources have a file at the same path, the
second copy gets its ``_N`` bumped to the next free slot in that timestamp
bucket so nothing overwrites anything else.

The destination ends up containing every file from every source — duplicates
included. The intended next step is ``find_duplicate_photos.py`` to flag the
overlaps for review.

Sources are read-only — this tool never deletes or modifies the source trees.

Run with::

    python combine_libraries.py --source "D:\\Files\\Pictures and Videos" \\
                                 --source "F:\\PhotosCopy" \\
                                 --dest "F:\\PhotosCombined"
    python combine_libraries.py --source ... --source ... --dest ... --dry-run

Both source and dest paths are required; the Tk picker is intentionally not
offered because typing multiple sources is fiddly via dialog.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil

from photo_lib.filename_pattern import CANONICAL_FILENAME_PARTS_RE
from photo_lib.logging_setup import configure_logging
from photo_lib.source_manifest import (
    SourceManifest,
    default_manifest_path,
    derive_source_label,
)

logger = logging.getLogger("photo_lib")


def _labels_for_sources(sources: list[str]) -> list[str]:
    """Derive a unique label per source. Raises ``SystemExit`` if two sources
    derive to the same label — the user has to disambiguate by passing distinct
    parent paths (or, eventually, an explicit ``--source-label`` flag)."""
    labels = [derive_source_label(s) for s in sources]
    seen: dict[str, str] = {}
    for source, label in zip(sources, labels):
        if label in seen:
            raise SystemExit(
                f"Source label collision: both {seen[label]!r} and {source!r} "
                f"derive to label {label!r}. Move one of the source folders or "
                f"rename it so the labels are distinct."
            )
        seen[label] = source
    return labels


def plan_combine(sources: list[str], dest: str) -> list[tuple[str, str, str]]:
    """Return ``[(src_path, dest_path, source_label), ...]`` for the combine.

    Order matters: a source that comes later loses ties (its ``_N`` gets
    bumped). ``source_label`` carries provenance through to the manifest so
    later steps can stamp ``__src_<label>`` onto marked filenames.

    Collision detection is CASE-INSENSITIVE — Windows NTFS, macOS HFS+, and
    most other consumer filesystems treat ``_1.JPG`` and ``_1.jpg`` as the
    same file. A naive case-sensitive comparison would plan both as separate
    dest paths and then shutil.copy2 would silently overwrite one with the
    other (losing one source's content while keeping the other source's
    name on disk). This bug ate 286 master files in the pilot run before it
    was caught.
    """
    labels = _labels_for_sources(sources)
    plan: list[tuple[str, str, str]] = []
    # Track what each destination subfolder will contain after the plan is
    # applied — both real files already there and files we've planned to add —
    # so collision detection works even before anything is copied. Names are
    # stored lower-cased so the comparison is case-insensitive.
    occupied: dict[str, set[str]] = {}

    def _occupied(folder: str) -> set[str]:
        if folder not in occupied:
            occupied[folder] = (
                {entry.name.lower() for entry in os.scandir(folder)}
                if os.path.isdir(folder) else set()
            )
        return occupied[folder]

    for source, label in zip(sources, labels):
        for current_dir, _subdirs, filenames in os.walk(source):
            relpath = os.path.relpath(current_dir, source)
            dest_folder = os.path.join(dest, relpath) if relpath != "." else dest
            slot_set = _occupied(dest_folder)
            for name in filenames:
                source_path = os.path.join(current_dir, name)
                # Resolve against what the dest WILL contain after planned copies,
                # not just what's on disk now. Case-fold the lookup.
                if name.lower() not in slot_set:
                    dest_name = name
                else:
                    dest_name = _resolve_dest_name_against_set(name, slot_set)
                slot_set.add(dest_name.lower())
                dest_path = os.path.join(dest_folder, dest_name)
                plan.append((source_path, dest_path, label))
    return plan


def _resolve_dest_name_against_set(source_name: str, occupied_names_lower: set[str]) -> str:
    """Return the next free dest name (case-insensitive collision-free).

    ``occupied_names_lower`` is expected to already be lower-cased by the
    caller — every candidate the loop produces is also lower-cased for the
    membership test, so collisions are detected regardless of source case.
    The returned name preserves the canonical_extension case it was built
    with (lower-case for canonical filenames; original-case stem for the
    non-canonical fallback).
    """
    match = CANONICAL_FILENAME_PARTS_RE.match(source_name)
    if match is not None:
        base = match.group("base")
        ext = match.group("ext")
        starting_idx = int(match.group("idx")) + 1
        n = starting_idx
        while True:
            candidate = f"{base}_{n}.{ext}"
            if candidate.lower() not in occupied_names_lower:
                return candidate
            n += 1
    stem, ext = os.path.splitext(source_name)
    counter = 1
    while True:
        candidate = f"{stem}_dup{counter}{ext}"
        if candidate.lower() not in occupied_names_lower:
            return candidate
        counter += 1


def apply_combine_plan(
    plan: list[tuple[str, str, str]],
    manifest: SourceManifest | None = None,
) -> int:
    copied = 0
    for source_path, dest_path, source_label in plan:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copy2(source_path, dest_path)
        if manifest is not None:
            manifest.set(dest_path, source_label)
        copied += 1
        if copied % 500 == 0:
            logger.info("  copied %d files", copied)
    return copied


def combine(sources: list[str], dest: str, dry_run: bool = False) -> int:
    plan = plan_combine(sources, dest)
    prefix = "[DRY-RUN] " if dry_run else ""
    renames = sum(
        1 for src, dst, _ in plan
        if os.path.basename(src) != os.path.basename(dst)
    )
    labels = sorted({label for _, _, label in plan})
    logger.info(
        "%s%d files to copy from %d source(s) — labels: %s "
        "(%d will be renamed on collision)",
        prefix, len(plan), len(labels), ", ".join(labels), renames,
    )
    if dry_run:
        for src, dst, label in plan[:25]:
            src_name = os.path.basename(src)
            dst_name = os.path.basename(dst)
            marker = " (RENAMED)" if src_name != dst_name else ""
            logger.info("  [%s] %s -> %s%s", label, src, dst, marker)
        if len(plan) > 25:
            logger.info("  ... and %d more", len(plan) - 25)
        return len(plan)
    if not plan:
        # Nothing to do — don't create an empty dest folder or a manifest with
        # no rows. Matches the historical "empty source = noop" contract.
        return 0
    os.makedirs(dest, exist_ok=True)
    with SourceManifest(default_manifest_path(dest)) as manifest:
        return apply_combine_plan(plan, manifest=manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Copy multiple photo libraries into one combined tree, bumping _N on collision."
    )
    parser.add_argument(
        "--source", action="append", required=True,
        help="Source library root. Pass --source multiple times for multiple sources.",
    )
    parser.add_argument(
        "--dest", required=True,
        help="Destination directory (created if needed).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show the planned copies without touching disk.",
    )
    cli_arguments = parser.parse_args()

    configure_logging("combine_libraries")
    combine(cli_arguments.source, cli_arguments.dest, dry_run=cli_arguments.dry_run)
