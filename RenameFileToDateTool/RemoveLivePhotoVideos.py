"""Find and quarantine the 1-3 second MOV/MP4 files that Google Takeout produces
when it splits an iOS Live Photo into a still + a short clip.

Heuristic: a video is treated as a Live Photo split when
  - its name stem matches a still image in the same folder (case-insensitive), and
  - its duration is under the threshold (default 5 seconds)

Matched videos are moved into a `_LivePhotoMOVs` subfolder rather than deleted,
so you can review before removing them permanently.
"""

import os
import shutil
import subprocess
import sys
import tkinter as tk
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog

FFPROBE_EXE = "ffprobe.exe"
QUARANTINE_FOLDER_NAME = "_LivePhotoMOVs"
DEFAULT_MAX_LIVE_PHOTO_DURATION_SECONDS = 5.0

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".gif"}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}


def select_folder_dialog(title: str) -> str | None:
    root = tk.Tk()
    root.withdraw()
    selected = filedialog.askdirectory(title=title)
    root.destroy()
    return selected or None


def get_video_duration_seconds(video_path: str) -> float | None:
    """Use ffprobe to return the duration of a video in seconds, or None on failure."""
    try:
        result = subprocess.run(
            [
                FFPROBE_EXE, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30,
        )
        duration_string = result.stdout.strip()
        if not duration_string:
            return None
        return float(duration_string)
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        return None


def group_files_by_stem(folder: str):
    """Return {lowercase_stem: {"images": [filename, ...], "videos": [filename, ...]}}."""
    groups = defaultdict(lambda: {"images": [], "videos": []})
    for filename in os.listdir(folder):
        full_path = os.path.join(folder, filename)
        if not os.path.isfile(full_path):
            continue
        stem, ext = os.path.splitext(filename)
        ext_lower = ext.lower()
        stem_lower = stem.lower()
        if ext_lower in IMAGE_EXTENSIONS:
            groups[stem_lower]["images"].append(filename)
        elif ext_lower in VIDEO_EXTENSIONS:
            groups[stem_lower]["videos"].append(filename)
    return groups


def find_live_photo_video_candidates(folder: str, max_duration: float) -> list[str]:
    """Return filenames of videos that look like Live Photo splits."""
    groups = group_files_by_stem(folder)

    candidate_paths = []
    for _stem, files in groups.items():
        if not files["images"] or not files["videos"]:
            continue
        for video_filename in files["videos"]:
            candidate_paths.append(os.path.join(folder, video_filename))

    if not candidate_paths:
        return []

    print(f"Checking duration of {len(candidate_paths)} paired video(s) via ffprobe...")
    matched = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        durations = list(executor.map(get_video_duration_seconds, candidate_paths))

    unreadable_count = 0
    for video_path, duration_seconds in zip(candidate_paths, durations):
        if duration_seconds is None:
            unreadable_count += 1
            continue
        if duration_seconds <= max_duration:
            matched.append(os.path.basename(video_path))

    if unreadable_count:
        print(f"Warning: ffprobe could not read duration for {unreadable_count} file(s) — those were left alone.")

    return matched


def quarantine_videos(folder: str, video_filenames: list[str]) -> int:
    quarantine_folder = os.path.join(folder, QUARANTINE_FOLDER_NAME)
    os.makedirs(quarantine_folder, exist_ok=True)
    moved = 0
    for filename in video_filenames:
        source_path = os.path.join(folder, filename)
        destination_path = os.path.join(quarantine_folder, filename)
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(destination_path):
            destination_path = os.path.join(quarantine_folder, f"{base}_dup{counter}{ext}")
            counter += 1
        shutil.move(source_path, destination_path)
        moved += 1
    return moved


def main():
    folder = select_folder_dialog("Select folder containing Takeout media")
    if not folder:
        print("No folder selected. Exiting.")
        return

    response = input(
        f"Max Live Photo duration in seconds (Enter for default {DEFAULT_MAX_LIVE_PHOTO_DURATION_SECONDS}): "
    ).strip()
    try:
        max_duration = float(response) if response else DEFAULT_MAX_LIVE_PHOTO_DURATION_SECONDS
    except ValueError:
        print("Invalid number — using default.")
        max_duration = DEFAULT_MAX_LIVE_PHOTO_DURATION_SECONDS

    matched_videos = find_live_photo_video_candidates(folder, max_duration)
    if not matched_videos:
        print("No Live Photo split videos found.")
        return

    print()
    print(f"Found {len(matched_videos)} short paired video(s):")
    for filename in matched_videos[:20]:
        print(f"  - {filename}")
    if len(matched_videos) > 20:
        print(f"  ... and {len(matched_videos) - 20} more")
    print()

    response = input(f"Move these into '{QUARANTINE_FOLDER_NAME}/' for review? [y/N]: ").strip().lower()
    if response != 'y':
        print("Aborted. No files moved.")
        return

    moved = quarantine_videos(folder, matched_videos)
    print(f"\nDone. {moved} file(s) moved into '{QUARANTINE_FOLDER_NAME}/'.")
    print("Review the folder; if you're happy, delete it manually.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
