import os
import subprocess
from PIL import Image
import tkinter as tk
from tkinter import filedialog

# Supported extensions for conversion
VIDEO_EXTENSIONS = ('.avi', '.3gp', '.gif')
IMAGE_EXTENSION = '.png'

def convert_video_to_mp4(input_file, output_folder):
    # Output file path
    output_file = os.path.join(output_folder, os.path.splitext(os.path.basename(input_file))[0] + '.mp4')

    # Run FFmpeg with explicit settings
    cmd = [
        'ffmpeg', '-i', input_file,   # Input file
        '-c:v', 'libx264',            # Convert video to H.264
        '-pix_fmt', 'yuv420p',        # Ensures compatibility (MJPEG may use non-standard pixel formats)
        '-c:a', 'aac',                # Convert audio to AAC (instead of Microsoft u-Law)
        '-b:a', '128k',               # Audio bitrate (standard for AAC)
        '-preset', 'slow',            # High-quality encoding
        '-crf', '18',                 # Best quality without overkill file size
        '-y',                         # Overwrite file if it exists
        output_file                    # Output file path
    ]

    # Run the conversion
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    print(f"Converted {input_file} to {output_file}")


def convert_image_to_jpg(input_file, output_folder):
    output_file = os.path.join(output_folder, os.path.splitext(os.path.basename(input_file))[0] + '.jpg')

    # Open PNG image and convert to JPG
    with Image.open(input_file) as img:
        img.convert("RGB").save(output_file, "JPEG")
    print(f"Converted {input_file} to {output_file}")


def convert_files(input_folder, output_folder):
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # Walk through the input folder and process files
    for root, _, files in os.walk(input_folder):
        for file in files:
            input_file = os.path.join(root, file)

            # Convert video files to MP4
            if file.lower().endswith(VIDEO_EXTENSIONS):
                convert_video_to_mp4(input_file, output_folder)

            # Convert PNG files to JPG
            elif file.lower().endswith(IMAGE_EXTENSION):
                convert_image_to_jpg(input_file, output_folder)


def main():
    # Open file dialog to select the folder containing input files
    root = tk.Tk()
    root.withdraw()  # Hide Tkinter window
    input_folder = filedialog.askdirectory(title="Select Folder Containing Files")

    if not input_folder:
        print("No folder selected. Exiting.")
        return

    # Select output folder
    output_folder = filedialog.askdirectory(title="Select Output Folder")

    if not output_folder:
        print("No output folder selected. Exiting.")
        return

    # Convert files
    convert_files(input_folder, output_folder)
    print("Conversion complete!")


if __name__ == "__main__":
    main()
