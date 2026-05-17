"""Ingest newly-renamed files from an inbox folder into the master photo library.

Workflow:
  1. Pick an inbox folder (defaults to <master>/_Inbox).
  2. Pick the master folder (D:\\Files\\Pictures and Videos).
  3. Validate filenames look like the rename script's output (YYYY-MM-DD HH.MM.SS...).
  4. Group files by year and print a plan.
  5. Pause so you can upload the inbox to Google Photos via the browser
     (or confirm you already did). This keeps cloud + master in sync.
  6. Move files into the right year folder (years 2000-2010 share one folder).
"""

import os
import re
import shutil
import sys
import tkinter as tk
from collections import defaultdict
from tkinter import filedialog, messagebox

DEFAULT_MASTER_ROOT = r"D:\Files\Pictures and Videos"
INBOX_FOLDER_NAME = "_Inbox"
BUNDLED_EARLY_FOLDER = "2000 - 2010"
BUNDLED_EARLY_YEARS = set(range(2000, 2011))

FILENAME_YEAR_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def select_folder_dialog(title: str, initialdir: str | None = None) -> str | None:
    root = tk.Tk()
    root.withdraw()
    selected = filedialog.askdirectory(title=title, initialdir=initialdir or "")
    root.destroy()
    return selected or None


def parse_year_from_filename(filename: str) -> int | None:
    match = FILENAME_YEAR_RE.match(filename)
    if not match:
        return None
    year = int(match.group(1))
    if year < 1900 or year > 2100:
        return None
    return year


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


def main():
    master_root = select_folder_dialog(
        "Select the master library folder",
        initialdir=DEFAULT_MASTER_ROOT if os.path.isdir(DEFAULT_MASTER_ROOT) else None,
    )
    if not master_root:
        print("No master folder selected. Exiting.")
        return

    default_inbox = os.path.join(master_root, INBOX_FOLDER_NAME)
    inbox_folder = select_folder_dialog(
        "Select the inbox folder (newly-renamed files)",
        initialdir=default_inbox if os.path.isdir(default_inbox) else master_root,
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
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
