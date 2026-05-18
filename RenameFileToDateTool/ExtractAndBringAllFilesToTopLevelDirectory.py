"""ExtractAndBringAllFilesToTopLevelDirectory.py — Takeout-zip flattener.

Google Takeout downloads come as a stack of large .zip files, each containing
``Takeout/Google Photos/<album>/<year>/<file>`` nested structure. This script:

  1. Extracts every .zip at the top level of the picked folder.
  2. Walks the resulting tree and moves every file up to a single
     ``Extracted data/`` folder.
  3. Resolves name collisions with ``_1``, ``_2`` suffixes.
  4. Deletes the now-empty intermediate folders.

The output is ready to feed into
``UpdateFileNameToDateFromGoogleTakeoutJSONMetadata.py``.

Run with ``python ExtractAndBringAllFilesToTopLevelDirectory.py``.
"""

from pathlib import Path
import shutil
import zipfile
from tkinter import messagebox
from concurrent.futures import ThreadPoolExecutor

from photo_lib.tk_picker import choose_directory


def unique_path(dest: Path, planned: set) -> Path:
    """Return a Path that doesn't exist on disk and isn't already planned.

    If dest is taken, suffix with _1, _2 … until a free name is found.
    """
    if not dest.exists() and dest.name not in planned:
        return dest
    stem, suffix = dest.stem, dest.suffix
    counter = 1
    while True:
        candidate = dest.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists() and candidate.name not in planned:
            return candidate
        counter += 1


def extract_zip(zip_path: Path, target_dir: Path) -> None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(target_dir)
        print(f"Unzipped {zip_path.name}")
    except zipfile.BadZipFile:
        print(f"{zip_path.name} is not a valid zip - skipped")


def flatten_takeout(root: Path) -> None:
    extracted_dir = root / "Extracted data"
    extracted_dir.mkdir(exist_ok=True)

    # 1. Extract every .zip in the root (Takeout chunks)
    for item in root.iterdir():
        if item.is_file() and item.suffix.lower() == ".zip":
            extract_zip(item, extracted_dir)

    # 2. Pre-compute all (source, destination) pairs sequentially
    #    so unique name resolution is deterministic before any moves start.
    planned = set()
    moves = []

    for path in root.rglob("*"):
        if path.is_dir() or path.is_symlink():
            continue
        if extracted_dir in path.parents:
            continue

        destination = unique_path(extracted_dir / path.name, planned)
        planned.add(destination.name)
        moves.append((path, destination))

    # 3. Execute all moves in parallel
    def do_move(pair):
        src, dst = pair
        shutil.move(str(src), str(dst))
        print(f"{src.name}  ->  {dst.name}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        executor.map(do_move, moves)

    # 4. Delete empty directories left behind
    for dir_ in sorted(root.rglob("*"), reverse=True):
        if dir_.is_dir() and not any(dir_.iterdir()):
            dir_.rmdir()

    messagebox.showinfo(
        "Completed",
        f"All files have been flattened into\n{extracted_dir}"
    )


def main() -> None:
    picked = choose_directory("Select Google-Takeout parent folder")
    if not picked:
        messagebox.showwarning("Cancelled", "No folder was selected.")
        return
    flatten_takeout(Path(picked))


if __name__ == "__main__":
    main()
