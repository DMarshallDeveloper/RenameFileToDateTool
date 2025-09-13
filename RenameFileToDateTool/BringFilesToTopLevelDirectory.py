import os
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox

def move_files_to_top_level(root_dir):
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        for filename in filenames:
            source_path = os.path.join(dirpath, filename)
            dest_path = os.path.join(root_dir, filename)

            if os.path.abspath(source_path) == os.path.abspath(dest_path):
                continue  # Skip if already in root

            base, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(dest_path):
                dest_path = os.path.join(root_dir, f"{base}_{counter}{ext}")
                counter += 1

            shutil.move(source_path, dest_path)

    messagebox.showinfo("Success", "All files have been moved to the top-level directory.")

def main():
    root = tk.Tk()
    root.withdraw()  # Hide the main window

    folder_path = filedialog.askdirectory(title="Select Folder to Flatten")
    if folder_path:
        move_files_to_top_level(folder_path)
    else:
        messagebox.showwarning("Cancelled", "No folder was selected.")

if __name__ == "__main__":
    main()
