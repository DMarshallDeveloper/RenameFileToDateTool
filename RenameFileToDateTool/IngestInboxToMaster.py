"""IngestInboxToMaster.py — move newly-named files into the master library.

The master photo library at ``D:\\Files\\Pictures and Videos\\`` is organised by
year folders (``2024/``, ``2025/``, …, plus a bundled ``2000 - 2010/`` for older
photos). New batches of photos — thumb drives, Takeout dumps, shared albums —
land first in ``D:\\Files\\Pictures and Videos\\_Inbox\\`` so it's always clear
what's "new and unmerged" vs what's already in the library.

This script handles step 2 of the ingest workflow:
  1. (You do this elsewhere) Run ``main.py`` or
     ``UpdateFileNameToDateFromGoogleTakeoutJSONMetadata.py`` to give every file
     in ``_Inbox/`` its canonical ``YYYY-MM-DD HH.MM.SS_N.ext`` name.
  2. Run this script:
     a. Pick the master folder (defaults to ``D:\\Files\\Pictures and Videos``).
     b. Pick the inbox folder (defaults to ``<master>/_Inbox``).
     c. The script groups files by year and prints what it'll move where.
     d. It pauses and asks you to upload ``_Inbox/`` to Google Photos in the
        browser. This keeps the cloud copy in sync. You can skip this if you've
        already uploaded.
     e. After you confirm, it moves the files into the right year folders.

Run with ``python IngestInboxToMaster.py``.
"""

import argparse
import os
import shutil
import sys
from collections import defaultdict

from photo_lib.config import (
    BUNDLED_EARLY_FOLDER,
    BUNDLED_EARLY_YEAR_RANGE,
    INBOX_FOLDER_NAME,
    MASTER_ROOT,
)
from photo_lib.filename_pattern import parse_filename_year
from photo_lib.tk_picker import choose_directory

DEFAULT_MASTER_ROOT = MASTER_ROOT
BUNDLED_EARLY_YEARS = set(range(*BUNDLED_EARLY_YEAR_RANGE))


def select_folder_dialog(title: str, initialdir: str | None = None) -> str | None:
    return choose_directory(title, initial_dir=initialdir)


def parse_year_from_filename(filename: str) -> int | None:
    return parse_filename_year(filename)


def target_folder_for_year(master_root: str, year: int) -> str:
    if year in BUNDLED_EARLY_YEARS:
        return os.path.join(master_root, BUNDLED_EARLY_FOLDER)
    return os.path.join(master_root, str(year))


def collision_safe_destination(destination_folder: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = filename
    counter = 1
    while os.path.exists(os.path.join(destination_folder, candidate)):
        candidate = f"{base}_dup{counter}{ext}"
        counter += 1
    return os.path.join(destination_folder, candidate)


def plan_moves(inbox_folder: str, master_root: str):
    """Return (moves, unparseable) where moves is a list of (src, dst, year) and
    unparseable is a list of filenames whose year couldn't be read."""
    moves = []
    unparseable = []
    for filename in sorted(os.listdir(inbox_folder)):
        source_path = os.path.join(inbox_folder, filename)
        if not os.path.isfile(source_path):
            continue
        year = parse_year_from_filename(filename)
        if year is None:
            unparseable.append(filename)
            continue
        destination_folder = target_folder_for_year(master_root, year)
        moves.append((source_path, destination_folder, filename, year))
    return moves, unparseable


def summarise_plan(moves, unparseable) -> None:
    counts_by_year = defaultdict(int)
    for _, _, _, year in moves:
        counts_by_year[year] += 1
    print()
    print("=== Plan ===")
    print(f"Total files to move: {len(moves)}")
    for year in sorted(counts_by_year):
        folder = BUNDLED_EARLY_FOLDER if year in BUNDLED_EARLY_YEARS else str(year)
        print(f"  {year} -> {folder}/  ({counts_by_year[year]} files)")
    if unparseable:
        print(f"\n{len(unparseable)} files have no parseable year and will be SKIPPED:")
        for filename in unparseable[:10]:
            print(f"  - {filename}")
        if len(unparseable) > 10:
            print(f"  ... and {len(unparseable) - 10} more")
    print()


def confirm_google_photos_upload(inbox_folder: str) -> bool:
    print("=== Google Photos upload ===")
    print("Before moving files into the master library, upload this inbox to Google")
    print("Photos so the cloud stays in sync.")
    print()
    print(f"  1. Open https://photos.google.com")
    print(f"  2. Drag the contents of {inbox_folder} into the browser window")
    print(f"  3. Wait for the upload to finish (watch the progress indicator)")
    print()
    while True:
        answer = input("Has the Google Photos upload finished? [y]es / [s]kip upload / [a]bort: ").strip().lower()
        if answer == 'y':
            return True
        if answer == 's':
            print("Skipping upload step.")
            return True
        if answer == 'a':
            return False


def execute_moves(moves) -> int:
    folders_created = set()
    moved = 0
    for source_path, destination_folder, filename, _year in moves:
        if destination_folder not in folders_created:
            os.makedirs(destination_folder, exist_ok=True)
            folders_created.add(destination_folder)
        destination_path = collision_safe_destination(destination_folder, filename)
        shutil.move(source_path, destination_path)
        moved += 1
        if moved % 50 == 0:
            print(f"  Moved {moved}/{len(moves)}")
    return moved


def _resolve_path(cli_value, title, initial_dir):
    """Pick a folder via CLI flag if given, otherwise via Tk picker.

    Validates that ``cli_value`` exists; aborts loudly if not. Returns None if the
    user cancelled the picker.
    """
    if cli_value:
        if not os.path.isdir(cli_value):
            print(f"Not a directory: {cli_value}")
            return None
        return cli_value
    return select_folder_dialog(title, initialdir=initial_dir)


def main(dry_run: bool = False, master_path: str | None = None,
         inbox_path: str | None = None, assume_yes: bool = False):
    master_root = _resolve_path(
        master_path,
        "Select the master library folder",
        DEFAULT_MASTER_ROOT if os.path.isdir(DEFAULT_MASTER_ROOT) else None,
    )
    if not master_root:
        print("No master folder selected. Exiting.")
        return

    default_inbox = os.path.join(master_root, INBOX_FOLDER_NAME)
    inbox_folder = _resolve_path(
        inbox_path,
        "Select the inbox folder (newly-renamed files)",
        default_inbox if os.path.isdir(default_inbox) else master_root,
    )
    if not inbox_folder:
        print("No inbox folder selected. Exiting.")
        return

    if os.path.abspath(inbox_folder) == os.path.abspath(master_root):
        print("Inbox folder cannot be the master folder itself. Exiting.")
        return

    moves, unparseable = plan_moves(inbox_folder, master_root)
    if not moves:
        print("No files with parseable year names found in the inbox.")
        if unparseable:
            print(f"({len(unparseable)} files were unparseable — rename them with main.py first.)")
        return

    summarise_plan(moves, unparseable)

    if dry_run:
        print(f"[DRY-RUN] Would move {len(moves)} files. Skipping Google Photos upload "
              "prompt and not touching disk.")
        return

    if assume_yes:
        print("--yes given: skipping Google Photos upload prompt and proceeding with moves.")
    else:
        if not confirm_google_photos_upload(inbox_folder):
            print("Aborted before moving files. Inbox is untouched.")
            return

        response = input(f"Move {len(moves)} files into master year folders now? [y/N]: ").strip().lower()
        if response != 'y':
            print("Aborted before moving files. Inbox is untouched.")
            return

    print(f"\nMoving {len(moves)} files...")
    moved = execute_moves(moves)
    print(f"\nDone. {moved} files moved into master library.")
    if unparseable:
        print(f"{len(unparseable)} unparseable files were left in the inbox.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Move newly-renamed files from the inbox into the master library's year folders."
    )
    parser.add_argument(
        "--master",
        help="Master library root. If omitted, opens the Tk folder picker."
    )
    parser.add_argument(
        "--inbox",
        help="Inbox folder containing the files to move. "
             "If omitted, opens the Tk folder picker (defaulting to <master>/_Inbox)."
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Auto-confirm the Google Photos upload prompt and the final move "
             "confirmation. Use for scripted/unattended runs."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the planned moves without touching disk or prompting for upload."
    )
    args = parser.parse_args()

    try:
        main(
            dry_run=args.dry_run,
            master_path=args.master,
            inbox_path=args.inbox,
            assume_yes=args.yes,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
