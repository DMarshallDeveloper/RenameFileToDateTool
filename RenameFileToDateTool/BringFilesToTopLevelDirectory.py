import os
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox


def compute_moves(root_dir):
    """Pre-compute all (source, destination) pairs without moving anything.

    Done sequentially so name collision resolution is deterministic.
    """
    planned_names = {
        f for f in os.listdir(root_dir)
        if os.path.isfile(os.path.join(root_dir, f))
    }
    moves = []

    for dirpath, _, filenames in os.walk(root_dir, topdown=False):
        if os.path.abspath(dirpath) == os.path.abspath(root_dir):
            continue  # Already at top level

        for filename in filenames:
            source_path = os.path.join(dirpath, filename)
            base, ext = os.path.splitext(filename)
            dest_name = filename
            counter = 1
            while dest_name in planned_names:
                dest_name = f"{base}_{counter}{ext}"
                counter += 1

            planned_names.add(dest_name)
            moves.append((source_path, os.path.join(root_dir, dest_name)))

    return moves


def move_files_to_top_level(root_dir):
    moves = compute_moves(root_dir)
    if not moves:
        messagebox.showinfo("Success", "All files are already in the top-level directory.")
        return

    total = len(moves)
    print(f"Moving {total} files...")
    for i, (src, dst) in enumerate(moves, 1):
        shutil.move(src, dst)
        if i % 50 == 0 or i == total:
            print(f"  Moved {i}/{total} files")

    messagebox.showinfo("Success", f"All {total} files have been moved to the top-level directory.")


def main():
    root = tk.Tk()
    root.withdraw()

    folder_path = filedialog.askdirectory(title="Select Folder to Flatten")
    if folder_path:
        move_files_to_top_level(folder_path)
    else:
        messagebox.showwarning("Cancelled", "No folder was selected.")


if __name__ == "__main__":
    main()
