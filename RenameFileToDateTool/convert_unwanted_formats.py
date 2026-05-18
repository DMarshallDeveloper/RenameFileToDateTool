"""convert_unwanted_formats.py — re-encode legacy video formats to mp4.

Some old container formats don't play nicely in Google Photos / iOS Photos, and
some (``.mpg`` in particular) can't even accept exiftool metadata writes — which
breaks the master library's "filename ≡ EXIF" invariant. This script transcodes
those formats to ``.mp4`` so the rest of the pipeline can handle them uniformly.

Targets:
  - ``.mpg`` — exiftool can't write metadata; iOS Photos won't import natively.
  - ``.avi`` / ``.3gp`` — old camcorder/phone formats, spotty viewer support.
  - ``.gif`` — animated GIFs become mp4 for proper video playback.
  - ``.mkv`` / ``.wmv`` / ``.flv`` / ``.mts`` / ``.m2ts`` — defensive coverage
    for anything that might land via Takeout or a thumb drive.

PNG conversion (.png → .jpg) is intentionally not included anymore: PNG → JPG
is lossless → lossy, which destroys the original. Most modern viewers handle
PNG fine. If you have problematic PNGs, decide on each individually rather
than bulk-converting.

``.aee`` (Apple Live Photo edit sidecar) is also excluded: it's a tiny XML
file, not video, and ffmpeg can't sensibly transcode it.

Uses ffmpeg. Originals are NOT deleted — you pick a separate output folder;
once you've verified the new files play correctly, you can delete the originals
yourself. Conversion failures get logged to ``conversion_errors.log``.

Run with ``python convert_unwanted_formats.py``.
"""

import argparse
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

from photo_lib.binaries import FFMPEG
from photo_lib.tk_picker import resolve_directory

FFMPEG_EXE = FFMPEG  # back-compat alias

# This converter is narrowly scoped: only the formats that genuinely cause
# problems downstream (un-writeable metadata, missing viewer support, etc).
# Don't reuse the canonical extension sets — this script needs its own narrow list.
VIDEO_EXTENSIONS = ('.avi', '.3gp', '.gif', '.mpg',
                    '.mkv', '.wmv', '.flv', '.mts', '.m2ts')
IMAGE_EXTENSIONS = ()  # PNG removed — lossless→lossy conversion is destructive


def convert_video_to_mp4(input_file, output_folder):
    output_file = os.path.join(
        output_folder,
        os.path.splitext(os.path.basename(input_file))[0] + '.mp4'
    )

    cmd = [
        FFMPEG_EXE, '-y', '-i', input_file,
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-preset', 'slow',
        '-crf', '18',
        output_file
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding='utf-8', errors='replace')
    if result.returncode == 0:
        print(f"Converted video {input_file} -> {output_file}")
    else:
        print(f"Failed to convert {input_file}")
        print(result.stderr)
        with open("conversion_errors.log", "a", encoding="utf-8") as f:
            f.write(f"{input_file}\n{result.stderr}\n\n")


def convert_image_to_jpg(input_file, output_folder):
    from PIL import Image, UnidentifiedImageError
    output_file = os.path.join(
        output_folder,
        os.path.splitext(os.path.basename(input_file))[0] + '.jpg'
    )

    try:
        with Image.open(input_file) as img:
            img.convert("RGB").save(output_file, "JPEG")
        print(f"Converted image {input_file} -> {output_file}")
    except UnidentifiedImageError:
        print(f"Skipping {input_file}, not a recognized image.")


def convert_files(input_folder, output_folder):
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    video_tasks = []
    image_tasks = []

    for root, _, files in os.walk(input_folder):
        for file in files:
            input_file = os.path.join(root, file)
            ext = file.lower()
            if ext.endswith(VIDEO_EXTENSIONS):
                video_tasks.append(input_file)
            elif ext.endswith(IMAGE_EXTENSIONS):
                image_tasks.append(input_file)
            else:
                print(f"Skipping {input_file}, unsupported type.")

    # Videos: ffmpeg is CPU-heavy so limit workers to avoid thrashing
    if video_tasks:
        with ThreadPoolExecutor(max_workers=4) as executor:
            executor.map(lambda f: convert_video_to_mp4(f, output_folder), video_tasks)

    # Images: PIL is fast and lightweight
    if image_tasks:
        with ThreadPoolExecutor(max_workers=8) as executor:
            executor.map(lambda f: convert_image_to_jpg(f, output_folder), image_tasks)


def main():
    parser = argparse.ArgumentParser(description="Transcode legacy formats (avi/3gp/gif/png/aee) to mp4/jpg.")
    parser.add_argument("--input", help="Folder containing files to convert. If omitted, opens the Tk folder picker.")
    parser.add_argument("--output", help="Destination folder for converted files. If omitted, opens the Tk folder picker.")
    args = parser.parse_args()

    input_folder = resolve_directory(args.input, "Select Folder Containing Files")
    if not input_folder:
        print("No folder selected. Exiting.")
        return

    output_folder = resolve_directory(args.output, "Select Output Folder", must_exist=False)
    if not output_folder:
        print("No output folder selected. Exiting.")
        return

    convert_files(input_folder, output_folder)
    print("Conversion complete!")


if __name__ == "__main__":
    main()
