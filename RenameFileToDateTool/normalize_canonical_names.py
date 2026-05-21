"""normalize_canonical_names.py — canonicalize extensions and renumber buckets.

Walks a tree of canonical-named photo files (``YYYY-MM-DD HH.MM.SS_N.ext``) and:

  - rewrites extensions to their canonical form (``.jpeg`` → ``.jpg``, all
    uppercase variants → lowercase),
  - renumbers each timestamp bucket so the ``_N`` indices form a contiguous
    ``_1, _2, _3, ...`` sequence globally across every extension.

Two consequences fall out for free:

  - **Gap-closing**: delete ``_3`` from a series and the next run pulls
    everything above it down by one.
  - **Cross-extension uniqueness within a timestamp**: a bucket can't hold
    both ``_1.jpg`` and ``_1.mp4``; the second bumps to ``_2.mp4``.

Run with::

    python normalize_canonical_names.py --path <folder>
    python normalize_canonical_names.py --path <folder> --dry-run

Defaults to live rename. Pass ``--dry-run`` to preview without touching disk.
If ``--path`` is omitted, a Tk folder picker opens.

Idempotent: running on an already-canonical tree is a no-op.
"""

import argparse
import logging
import os

from photo_lib.canonical_renumber import (
    apply_rename_plan,
    plan_renames_recursive,
)
from photo_lib.logging_setup import configure_logging
from photo_lib.source_manifest import SourceManifest, default_manifest_path
from photo_lib.tk_picker import resolve_directory

logger = logging.getLogger("photo_lib")


def normalize_tree(directory: str, dry_run: bool = False) -> None:
    if not directory:
        logger.info("No directory selected. Exiting.")
        return

    plans_by_folder = plan_renames_recursive(directory)
    total_renames = sum(len(plan) for plan in plans_by_folder.values())
    if total_renames == 0:
        logger.info("Nothing to do — tree already canonical.")
        return

    prefix = "[DRY-RUN] " if dry_run else ""
    logger.info("%s%d renames planned across %d folders.",
                prefix, total_renames, len(plans_by_folder))

    # If a combine-written source manifest exists alongside the library, keep
    # its path keys in sync with every rename — otherwise `find_duplicate_photos
    # mark` would lose source-label provenance for any file the canonicalizer
    # touched (e.g. .jpeg → .jpg).
    manifest_path = default_manifest_path(directory)
    use_manifest = (not dry_run) and os.path.exists(manifest_path)

    def _apply_with_manifest(plan):
        if not use_manifest:
            return apply_rename_plan(plan)
        applied = apply_rename_plan(plan)
        # rename_many staged via temp keys — the plan can include
        # "_2.MOV -> _4.mov" while another entry already occupies _4.mov,
        # which a single-row UPDATE would reject on the UNIQUE constraint.
        with SourceManifest(manifest_path) as manifest:
            manifest.rename_many([(r.old_path, r.new_path) for r in plan])
        return applied

    for folder, plan in sorted(plans_by_folder.items()):
        folder_label = os.path.relpath(folder, directory)
        logger.info("%s[%s] %d renames", prefix, folder_label, len(plan))
        for rename in plan:
            logger.debug(
                "%s  %s  ->  %s    (%s)",
                prefix,
                os.path.basename(rename.old_path),
                os.path.basename(rename.new_path),
                rename.reason,
            )
        if not dry_run:
            applied = _apply_with_manifest(plan)
            logger.info("  applied: %d", applied)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Canonicalize extensions and renumber timestamp buckets."
    )
    parser.add_argument(
        "--path",
        help="Directory to operate on (recursively). If omitted, opens the Tk "
             "folder picker.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show the planned renames without touching disk.",
    )
    cli_arguments = parser.parse_args()

    configure_logging("normalize_canonical_names")
    target_directory = resolve_directory(cli_arguments.path, "Select Photos Directory")
    normalize_tree(target_directory, dry_run=cli_arguments.dry_run)
