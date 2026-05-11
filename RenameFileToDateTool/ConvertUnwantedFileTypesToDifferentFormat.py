import os
import subprocess
import tkinter as tk
from tkinter import filedialog
from concurrent.futures import ThreadPoolExecutor

# Treat .aee as video
VIDEO_EXTENSIONS = ('.avi', '.3gp', '.gif', '.aee')
IMAGE_EXTENSIONS = ('.png',)  # only keep real images


def convert_video_to_mp4(input_file, output_folder):
    output_file = os.path.join(
        output_folder,
        os.path.splitext(os.path.basename(input_file))[0] + '.mp4'
    )

    cmd = [
        'ffmpeg', '-y', '-i', input_file,
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-preset', 'slow',
        '-crf', '18',
        output_file
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
    root = tk.Tk()
    root.withdraw()
    input_folder = filedialog.askdirectory(title="Select Folder Containing Files")
    if not input_folder:
        print("No folder selected. Exiting.")
        return

    output_folder = filedialog.askdirectory(title="Select Output Folder")
    if not output_folder:
        print("No output folder selected. Exiting.")
        return

    convert_files(input_folder, output_folder)
    print("Conversion complete!")


if __name__ == "__main__":
    main()
