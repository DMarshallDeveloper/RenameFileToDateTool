# flatten_takeout.py
from pathlib import Path
import shutil
import zipfile
import tkinter as tk
from tkinter import filedialog, messagebox


def unique_path(dest: Path) -> Path:
    """
    If “dest” already exists, return a new Path whose filename
    is suffixed with _1, _2 … until it is unique.
    """
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    counter = 1
    while True:
        candidate = dest.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def extract_zip(zip_path: Path, target_dir: Path) -> None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(target_dir)
        print(f"✓ Unzipped {zip_path.name}")
    except zipfile.BadZipFile:
        print(f"✗ {zip_path.name} is not a valid zip – skipped")


def flatten_takeout(root: Path) -> None:
    extracted_dir = root / "Extracted data"
    extracted_dir.mkdir(exist_ok=True)

    # 1️⃣  Extract every .zip sitting in the root (Takeout chunks)
    for item in root.iterdir():
        if item.is_file() and item.suffix.lower() == ".zip":
            extract_zip(item, extracted_dir)

    # 2️⃣  Walk everything under root and move *files* up.
    #     rglob("*") sees what the zips just unpacked as well.
    for path in root.rglob("*"):
        if path.is_dir() or path.is_symlink():
            continue
        # Skip things already inside “Extracted data”
        if extracted_dir in path.parents:
            continue

        destination = unique_path(extracted_dir / path.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(destination))
        print(f"→ {path}  →  {destination.name}")

    # 3️⃣  (Optional) delete empty dirs that were left behind
    for dir_ in sorted(root.rglob("*"), reverse=True):
        if dir_.is_dir() and not any(dir_.iterdir()):
            dir_.rmdir()

    messagebox.showinfo(
        "Completed",
        f"All files have been flattened into\n{extracted_dir}"
    )


def main() -> None:
    tk.Tk().withdraw()  # hide root window
    picked = filedialog.askdirectory(title="Select Google-Takeout parent folder")
    if not picked:
        messagebox.showwarning("Cancelled", "No folder was selected.")
        return
    flatten_takeout(Path(picked))


if __name__ == "__main__":
    main()