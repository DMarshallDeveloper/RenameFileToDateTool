"""Plan and apply canonical-name rebucketing for one folder.

Two transformations happen in a single pass:

1. **Extension canonicalization**: ``jpeg`` → ``jpg``; uppercase variants
   (``.JPG``, ``.HEIC``, ``.MOV``, etc.) → lowercase. See
   ``photo_lib.extensions.canonical_extension``.

2. **Per-timestamp renumbering**: within each base-timestamp bucket
   (``YYYY-MM-DD HH.MM.SS``), files are reassigned ``_1, _2, _3, ...``
   globally across every extension. Order: ascending current ``_N``, then
   extension alphabetical — preserves the existing relative order. Two side
   effects fall out for free:

   - **Gap-closing**: if you delete ``_3`` from a series, the next run pulls
     ``_4`` down to ``_3`` and so on.
   - **Cross-extension uniqueness**: a bucket can no longer hold both
     ``_1.jpg`` and ``_1.mp4``; the second gets bumped to ``_2.mp4``. This
     undoes the per-extension counter that an older takeout-ingest used.

Renames are staged via a temp-name pass so mid-rename collisions never happen
even when the new name of file A equals the old name of file B.
"""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass

from photo_lib.extensions import canonical_extension
from photo_lib.filename_pattern import CANONICAL_FILENAME_PARTS_RE


@dataclass(frozen=True)
class PlannedRename:
    old_path: str
    new_path: str
    reason: str


def plan_renames_for_folder(folder: str) -> list[PlannedRename]:
    """Return the list of renames that would canonicalize ``folder`` (one level only).

    Non-canonical filenames in the folder are left alone — this helper assumes
    the audit / detect_malformed_filenames pass has already flagged them.
    """
    buckets: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
    for entry in os.scandir(folder):
        if not entry.is_file():
            continue
        match = CANONICAL_FILENAME_PARTS_RE.match(entry.name)
        if not match:
            continue
        base = match.group("base")
        idx = int(match.group("idx"))
        ext_as_written = match.group("ext")
        new_ext = canonical_extension(ext_as_written)
        buckets[base].append((idx, ext_as_written, new_ext, entry.path))

    planned: list[PlannedRename] = []
    for base, items in buckets.items():
        items.sort(key=lambda t: (t[0], t[1]))
        for new_idx, (old_idx, old_ext, new_ext, old_path) in enumerate(items, start=1):
            new_name = f"{base}_{new_idx}.{new_ext}"
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            if new_path == old_path:
                continue
            reason_parts: list[str] = []
            if new_ext != old_ext:
                reason_parts.append(f"ext {old_ext}->{new_ext}")
            if new_idx != old_idx:
                reason_parts.append(f"idx {old_idx}->{new_idx}")
            planned.append(PlannedRename(old_path, new_path, ", ".join(reason_parts)))
    return planned


def apply_rename_plan(plan: list[PlannedRename]) -> int:
    """Execute the rename plan using a two-phase staging pass.

    Phase 1 renames each source to a temp name so any "A's new name was B's old
    name" cycle doesn't collide. Phase 2 renames each temp file to its target.
    Returns the number of renames applied.
    """
    if not plan:
        return 0
    staged: list[tuple[str, str]] = []
    for rename in plan:
        temp_path = rename.old_path + ".__renaming__"
        os.rename(rename.old_path, temp_path)
        staged.append((temp_path, rename.new_path))
    for temp_path, new_path in staged:
        os.rename(temp_path, new_path)
    return len(staged)


def plan_renames_recursive(root: str) -> dict[str, list[PlannedRename]]:
    """Return ``{folder_path: [PlannedRename, ...]}`` for every folder under ``root``.

    Each folder is its own bucket-space — timestamps don't cross folder
    boundaries. ``root`` itself is included if it contains canonical files.
    """
    result: dict[str, list[PlannedRename]] = {}
    for current_dir, _subdirs, _filenames in os.walk(root):
        plan = plan_renames_for_folder(current_dir)
        if plan:
            result[current_dir] = plan
    return result
