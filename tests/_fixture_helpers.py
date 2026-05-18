"""Test fixture helpers: build small reproducible image/video files with known EXIF.

Fixtures are cached under tests/fixtures/ after first build so repeated test runs are fast.
"""

import os
import shutil
import subprocess
import sys

from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.join(REPO_ROOT, "RenameFileToDateTool")
sys.path.insert(0, SCRIPT_DIR)

from photo_lib.binaries import EXIFTOOL, FFMPEG  # noqa: E402

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# Canonical fixture dates. The image uses local-time tags; the video uses the
# UTC equivalent (NZ January = UTC+13 NZDT, so 14:30:45 local == 01:30:45 UTC).
FIXTURE_IMAGE_LOCAL_DATETIME = "2026:01:15 14:30:45"
FIXTURE_VIDEO_UTC_DATETIME = "2026:01:15 01:30:45"
FIXTURE_VIDEO_EXPECTED_LOCAL = "2026:01:15 14:30:45"


def make_image_with_tz(target_dir, name, datetime_local, offset):
    """Create a JPG with explicit OffsetTimeOriginal — simulates a photo taken overseas."""
    src, _ = ensure_fixtures()
    import shutil
    dst = os.path.join(target_dir, name)
    shutil.copy2(src, dst)
    _run([
        EXIFTOOL,
        f'-DateTimeOriginal={datetime_local}',
        f'-CreateDate={datetime_local}',
        f'-OffsetTimeOriginal={offset}',
        f'-OffsetTime={offset}',
        '-overwrite_original', dst,
    ])
    return dst


def make_video_with_tz(target_dir, name, datetime_utc, datetime_local, offset):
    """Create a MOV with a CreationDate that carries an explicit TZ offset."""
    _, src = ensure_fixtures()
    import shutil
    dst = os.path.join(target_dir, name)
    shutil.copy2(src, dst)
    _run([
        EXIFTOOL,
        f'-MediaCreateDate={datetime_utc}',
        f'-TrackCreateDate={datetime_utc}',
        f'-CreateDate={datetime_utc}',
        f'-CreationDate={datetime_local}{offset}',
        '-overwrite_original', dst,
    ])
    return dst


def ensure_fixtures():
    """Build cached fixture files if missing. Returns (image_path, video_path)."""
    os.makedirs(FIXTURES_DIR, exist_ok=True)
    image_path = os.path.join(FIXTURES_DIR, "sample_image.jpg")
    video_path = os.path.join(FIXTURES_DIR, "sample_video.mov")

    if not os.path.exists(image_path):
        Image.new('RGB', (16, 16), color=(200, 50, 50)).save(image_path, 'JPEG')
        _run([
            EXIFTOOL,
            f'-DateTimeOriginal={FIXTURE_IMAGE_LOCAL_DATETIME}',
            f'-CreateDate={FIXTURE_IMAGE_LOCAL_DATETIME}',
            f'-ModifyDate={FIXTURE_IMAGE_LOCAL_DATETIME}',
            '-overwrite_original', image_path,
        ])

    if not os.path.exists(video_path):
        _run([
            FFMPEG, '-y',
            '-f', 'lavfi', '-i', 'color=c=blue:s=16x16:d=1:r=1',
            '-pix_fmt', 'yuv420p',
            video_path,
        ])
        _run([
            EXIFTOOL,
            f'-MediaCreateDate={FIXTURE_VIDEO_UTC_DATETIME}',
            f'-MediaModifyDate={FIXTURE_VIDEO_UTC_DATETIME}',
            f'-TrackCreateDate={FIXTURE_VIDEO_UTC_DATETIME}',
            f'-TrackModifyDate={FIXTURE_VIDEO_UTC_DATETIME}',
            f'-CreateDate={FIXTURE_VIDEO_UTC_DATETIME}',
            f'-ModifyDate={FIXTURE_VIDEO_UTC_DATETIME}',
            '-overwrite_original', video_path,
        ])

    return image_path, video_path


def copy_fixture_image(target_dir, name=None):
    src, _ = ensure_fixtures()
    dst = os.path.join(target_dir, name or os.path.basename(src))
    shutil.copy2(src, dst)
    return dst


def copy_fixture_video(target_dir, name=None):
    _, src = ensure_fixtures()
    dst = os.path.join(target_dir, name or os.path.basename(src))
    shutil.copy2(src, dst)
    return dst


def read_exif_tag(path, tag):
    """Read a single EXIF tag, returning the raw string value."""
    result = subprocess.run(
        [EXIFTOOL, f'-{tag}', '-s', '-s', '-s', path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding='utf-8', errors='replace',
    )
    return result.stdout.strip()


def _run(cmd):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding='utf-8', errors='replace')
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
