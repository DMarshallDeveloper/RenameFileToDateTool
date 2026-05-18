"""RemoveLivePhotoVideos.py — quarantine the 1-3 second video clips that come
from iOS Live Photos.

iOS Live Photos are a still image plus a tiny accompanying video. When you download
them from Google Takeout, they arrive as two separate files with the same name stem
(e.g. ``IMG_3118.HEIC`` + ``IMG_3118.MP4``). Most of the time those mini-videos
are noise — auto-captured motion before/after the real shot — and you don't want
them in your library.

This script finds those mini-videos by looking for:
  - a video whose filename stem matches a still image in the same folder, AND
  - whose duration (read via ``ffprobe``) is under the threshold (default 5 s).

It MOVES matched videos into a ``_LivePhotoMOVs/`` subfolder rather than deleting
them, so you can review before binning anything permanently.

WARNING: per the user's photo-library workflow, this is an OPTIONAL tool. The
default ingest workflow keeps the Live Photo videos alongside the stills. Only
use this when you've decided a particular batch of clips is genuinely junk.

Run with ``python RemoveLivePhotoVideos.py``.
"""

import os
import argparse
import shutil
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from photo_lib.binaries import FFPROBE
from photo_lib.extensions import is_image, normalize_extension
from photo_lib.tk_picker import choose_directory, resolve_directory

FFPROBE_EXE = FFPROBE  # back-compat alias for tests that reference it
QUARANTINE_FOLDER_NAME = "_LivePhotoMOVs"
DEFAULT_MAX_LIVE_PHOTO_DURATION_SECONDS = 5.0

# Live Photo pairs are always still + short clip. Audio-only and unusual containers
# like .mkv don't come out of iOS Live Photos, so we restrict to the three formats
# Apple/Takeout actually produce (mov/mp4/m4v).
_LIVE_PHOTO_VIDEO_EXTENSIONS = {"mov", "mp4", "m4v"}


def _is_live_photo_video(ext: str) -> bool:
    return normalize_extension(ext) in _LIVE_PHOTO_VIDEO_EXTENSIONS


def select_folder_dialog(title: str) -> str | None:
    return choose_directory(title)


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
            encoding='utf-8', errors='replace', timeout=30,
        )
        duration_string = result.stdout.strip()
        if not duration_string:
            return None
        return float(duration_string)
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        return None


def group_files_by_stem(folder: str):
    """Return ``{lowercase_stem: {"images": [filename, ...], "videos": [filename, ...]}}``.

    Videos are restricted to mov/mp4/m4v (iOS Live Photo formats) — other video types
    aren't produced by Apple/Takeout Live Photo splits and shouldn't be quarantined.
    """
    groups = defaultdict(lambda: {"images": [], "videos": []})
    for filename in os.listdir(folder):
        full_path = os.path.join(folder, filename)
        if not os.path.isfile(full_path):
            continue
        stem, ext = os.path.splitext(filename)
        stem_lower = stem.lower()
        if is_image(ext):
            groups[stem_lower]["images"].append(filename)
        elif _is_live_photo_video(ext):
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


def main(path: str | None = None,
         max_duration: float | None = None,
         assume_yes: bool = False):
    folder = resolve_directory(path, "Select folder containing Takeout media")
    if not folder:
        print("No folder selected. Exiting.")
        return

    if max_duration is None:
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

    if assume_yes:
        print(f"--yes given: moving into '{QUARANTINE_FOLDER_NAME}/'.")
    else:
        response = input(f"Move these into '{QUARANTINE_FOLDER_NAME}/' for review? [y/N]: ").strip().lower()
        if response != 'y':
            print("Aborted. No files moved.")
            return

    moved = quarantine_videos(folder, matched_videos)
    print(f"\nDone. {moved} file(s) moved into '{QUARANTINE_FOLDER_NAME}/'.")
    print("Review the folder; if you're happy, delete it manually.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Find iOS Live Photo split videos and quarantine them for review."
    )
    parser.add_argument("--path", help="Folder to scan. If omitted, opens the Tk folder picker.")
    parser.add_argument(
        "--max-duration", type=float,
        help=f"Max duration in seconds for a clip to count as a Live Photo split "
             f"(default {DEFAULT_MAX_LIVE_PHOTO_DURATION_SECONDS}). "
             "If omitted, prompts interactively."
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip the quarantine confirmation prompt."
    )
    args = parser.parse_args()

    try:
        main(path=args.path, max_duration=args.max_duration, assume_yes=args.yes)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
