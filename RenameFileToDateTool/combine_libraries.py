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

logger = logging.getLogger("photo_lib")


def plan_combine(sources: list[str], dest: str) -> list[tuple[str, str]]:
    """Return ``[(src_path, dest_path), ...]`` for the combine. Order matters:
    a source that comes later loses ties (its ``_N`` gets bumped)."""
    plan: list[tuple[str, str]] = []
    # Track what each destination subfolder will contain after the plan is
    # applied — both real files already there and files we've planned to add —
    # so collision detection works even before anything is copied.
    occupied: dict[str, set[str]] = {}

    def _occupied(folder: str) -> set[str]:
        if folder not in occupied:
            occupied[folder] = (
                {entry.name for entry in os.scandir(folder)}
                if os.path.isdir(folder) else set()
            )
        return occupied[folder]

    for source in sources:
        for current_dir, _subdirs, filenames in os.walk(source):
            relpath = os.path.relpath(current_dir, source)
            dest_folder = os.path.join(dest, relpath) if relpath != "." else dest
            slot_set = _occupied(dest_folder)
            for name in filenames:
                source_path = os.path.join(current_dir, name)
                # Resolve against what the dest WILL contain after planned copies,
                # not just what's on disk now.
                if name not in slot_set:
                    dest_name = name
                else:
                    dest_name = _resolve_dest_name_against_set(name, slot_set)
                slot_set.add(dest_name)
                dest_path = os.path.join(dest_folder, dest_name)
                plan.append((source_path, dest_path))
    return plan


def _resolve_dest_name_against_set(source_name: str, occupied_names: set[str]) -> str:
    match = CANONICAL_FILENAME_PARTS_RE.match(source_name)
    if match is not None:
        base = match.group("base")
        ext = match.group("ext")
        starting_idx = int(match.group("idx")) + 1
        n = starting_idx
        while True:
            candidate = f"{base}_{n}.{ext}"
            if candidate not in occupied_names:
                return candidate
            n += 1
    stem, ext = os.path.splitext(source_name)
    counter = 1
    while True:
        candidate = f"{stem}_dup{counter}{ext}"
        if candidate not in occupied_names:
            return candidate
        counter += 1


def apply_combine_plan(plan: list[tuple[str, str]]) -> int:
    copied = 0
    for source_path, dest_path in plan:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copy2(source_path, dest_path)
        copied += 1
        if copied % 500 == 0:
            logger.info("  copied %d files", copied)
    return copied


def combine(sources: list[str], dest: str, dry_run: bool = False) -> int:
    plan = plan_combine(sources, dest)
    prefix = "[DRY-RUN] " if dry_run else ""
    renames = sum(
        1 for src, dst in plan
        if os.path.basename(src) != os.path.basename(dst)
    )
    logger.info(
        "%s%d files to copy (%d will be renamed on collision)",
        prefix, len(plan), renames,
    )
    if dry_run:
        for src, dst in plan[:25]:
            src_name = os.path.basename(src)
            dst_name = os.path.basename(dst)
            marker = " (RENAMED)" if src_name != dst_name else ""
            logger.info("  %s -> %s%s", src, dst, marker)
        if len(plan) > 25:
            logger.info("  ... and %d more", len(plan) - 25)
        return len(plan)
    return apply_combine_plan(plan)


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
