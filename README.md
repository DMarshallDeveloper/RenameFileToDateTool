# RenameFileToDateTool

A collection of Python scripts for keeping a personal photo library organised
and synced between a Windows laptop, Google Photos, and an iPhone.

The core problem: photos arrive from many sources (iPhone, Google Takeout
exports, family thumb drives, dance-competition shared albums) with
inconsistent filenames, broken EXIF dates, and timezone confusion. Without a
canonical naming scheme, photos end up out of order whenever they're shuffled
between Google Photos / Drive / iOS / the laptop.

These scripts give every photo a consistent filename based on when it was
actually taken (e.g. `2026-04-09 19.52.51_1.jpg`), and keep the embedded EXIF
metadata in sync with the filename so the date survives any future sync.

## Quick start

```
git clone <repo>
cd RenameFileToDateTool
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File RenameFileToDateTool\bin\download.ps1
python -m unittest discover -s tests
```

The `download.ps1` step fetches `ffmpeg.exe` and `ffprobe.exe` from the
Gyan.dev release-essentials build (they're too large to commit). `exiftool.exe`
is committed and works out of the box.

If your master library lives somewhere other than the default
`D:\Files\Pictures and Videos\`, or your default timezone isn't NZ, edit
`RenameFileToDateTool/photo_lib/config.py`.

To use a script, run it directly with Python. A folder picker dialog will
open; pick the folder you want to operate on.

```
cd RenameFileToDateTool
python rename_files_from_exif.py --path <some-folder>
# or:
python write_exif_from_filename.py --path <some-folder>
```

## Repository layout

```
RenameFileToDateTool/                 ← repo root
├── README.md                         ← you are here
├── .gitignore
├── audit_master.py                   ← read-only diagnostic for the master library
├── tests/                            ← unit + integration tests (see "Tests" below)
└── RenameFileToDateTool/             ← the actual Python package
    ├── bin/                          ← bundled command-line tools
    │   ├── download.ps1              ← fetches ffmpeg.exe + ffprobe.exe (run once)
    │   ├── exiftool.exe              ← EXIF/QuickTime metadata reader+writer (committed)
    │   ├── ffmpeg.exe                ← video transcoder (gitignored, fetched on demand)
    │   └── ffprobe.exe               ← video metadata reader (gitignored, fetched on demand)
    ├── logs/                         ← runtime error logs (gitignored)
    ├── photo_lib/                    ← shared library (don't run directly)
    │   ├── binaries.py               ← paths to the three .exe tools
    │   ├── config.py                 ← master root, default TZ, inbox name — edit for your machine
    │   ├── exiftool_runner.py        ← batched exiftool calls (read + write)
    │   ├── extensions.py             ← canonical IMAGE / VIDEO extension sets
    │   ├── filename_pattern.py       ← regex + parse_filename_datetime + placeholder bump
    │   ├── tag_modes.py              ← which EXIF/QuickTime tags get written and how
    │   ├── takeout_geo.py            ← GPS → timezone lookup for Takeout JSONs
    │   ├── timezone_detection.py     ← per-file TZ detection (CreationDate, Offset*)
    │   └── tk_picker.py              ← reusable folder-picker dialog wrapper
    └── *.py                          ← runnable scripts (see "Scripts" below)
```

## The two photo libraries you should know about

1. **Master library** — `D:\Files\Pictures and Videos\` — year-organised folders
   (`2024/`, `2025/`, …, plus a bundled `2000 - 2010/` for older photos).
   Source of truth. Backed up to Google Drive automatically.

2. **Inbox** — `D:\Files\Pictures and Videos\_Inbox\` — staging area where every
   new batch lands before being merged into the master. The rule: nothing goes
   straight into a year folder. Always route through `_Inbox/` first so it's
   clear what's "new and unmerged" vs what's already in the library.

## The three key scripts (start here)

### `rename_files_from_exif.py` — rename to canonical names

Every file in the picked folder gets renamed to `YYYY-MM-DD HH.MM.SS_N.ext`,
with the date pulled from its embedded metadata. Idempotent — re-running on
an already-renamed folder is a no-op.

### `write_exif_from_filename.py` — sync metadata to filename

The mirror image: filenames stay, embedded metadata gets rewritten to match.
Use when filenames are correct but EXIF is broken (typical after Google
Takeout). For a recursive variant that sweeps every year folder in one pass,
see `ChangeDatesFromFileName.py`.

### `audit_master.py` — read-only diagnostic

Samples a few files from each year folder, checks whether filename and EXIF
match, plus catches structural drift (extension mismatches, files in the wrong
year folder, non-canonical names). Use this to find folders that need a
`write_exif_from_filename.py` pass. **Doesn't modify anything.**

## Pipeline scripts (Google Takeout → master library)

### Quick path: `process_takeout.py` (one command)

```
python process_takeout.py --takeout C:\Users\<you>\Downloads\takeout-...
```

Runs both steps below in sequence, depositing canonical-named files in
`<MASTER_ROOT>/_Inbox/<takeout-folder-basename>/`. From there, drag the files
to the right year folder or run `IngestInboxToMaster.py`.

### What the orchestrator does internally

1. **`ExtractAndBringAllFilesToTopLevelDirectory.py`** — unzip the Takeout
   archives and flatten the nested `Takeout/Google Photos/<album>/<year>/`
   structure into a single folder.

2. **`UpdateFileNameToDateFromGoogleTakeoutJSONMetadata.py`** — pair each media
   file with its `.json` sidecar (which holds the real photo-taken time),
   convert the UTC timestamp to local time at the GPS location (via
   `timezonefinder`), and copy the file to a destination with a canonical name.

You can still run those two scripts separately if you want to inspect the
intermediate state — they each take `--path` (or `--src`/`--dst`) flags. The
orchestrator just chains them together.

### After the orchestrator

3. **`IngestInboxToMaster.py`** moves the staging files into the right year
   folders (with the usual Google Photos upload prompt).

4. **`IngestInboxToMaster.py`** — moves files from `_Inbox/` into the right year
   folder. Pauses first to remind you to upload `_Inbox/` to Google Photos in
   the browser, so the cloud stays in sync.

## Other scripts

### Cleanup & flattening

- **`BringFilesToTopLevelDirectory.py`** — flatten a folder tree (every file
  moves up to the root, no contents copied).
- **`SplitMediaIntoFolders.py`** — break a big folder into batches of 100
  files. Used for staging uploads to the iPhone via Google Drive.
- **`RemoveLivePhotoVideos.py`** — *optional* — find and quarantine the
  1-3-second video clips from iOS Live Photo splits. Off by default; the
  master library normally keeps these.

### Format conversion

- **`ConvertUnwantedFileTypesToDifferentFormat.py`** — transcode legacy formats
  (`.avi`, `.3gp`, `.gif` videos, `.png` images) to `.mp4` / `.jpg`.
- **`CopyUnwantedFileTypeFilesToSeparateFolder.py`** — gather those legacy
  files into a single folder for review before transcoding.

### Diagnostics (all read-only)

- **`DetectMalformedFileNames.py`** — print any filenames not matching the
  master library's canonical `YYYY-MM-DD HH.MM.SS_N.ext` pattern.
- **`CountFileExtensionsInFolder.py`** — count files by extension (recursive).
- **`CountFileExtensionsInFolderWithExif.py`** — same, but uses exiftool to
  ask what each file *really* is (some `.jpg`s are HEIC bytes in disguise).
- **`FindBurstFiles.py`** — list iOS burst-mode photos (sharing a `BurstUUID`).
- **`FindFolderDifferences.py`** — print files unique to folder A vs folder B.
- **`ListAllFilesInFolder.py`** — trivial: print every filename under a folder.
- **`ChangeDatesFromFileName.py`** — recursive version of `write_exif_from_filename.py`.

## The shared library — `photo_lib/`

Don't run any of these directly — they're imported by the scripts above.

| File                              | What it owns                                                                                  |
|-----------------------------------|-----------------------------------------------------------------------------------------------|
| `binaries.py`                     | Paths to bundled `exiftool.exe` / `ffmpeg.exe` / `ffprobe.exe`.                              |
| `config.py`                       | Master library root, default timezone, inbox folder name, audit sample size.                |
| `exiftool_runner.py`              | Batched exiftool calls. Uses a `-@ filelist` temp file to avoid the Windows 32k cmdline cap. |
| `extensions.py`                   | Canonical `IMAGE_EXTENSIONS`, `VIDEO_EXTENSIONS`, `MEDIA_EXTENSIONS` (frozen sets).          |
| `filename_pattern.py`             | Regexes and helpers for parsing dates from filenames; the Jan-1 placeholder bump.            |
| `tag_modes.py`                    | Which EXIF / QuickTime tags get written and in what format (local naive, UTC, or local+TZ). |
| `takeout_geo.py`                  | GPS → timezone lookup for Google Takeout JSON metadata.                                       |
| `timezone_detection.py`           | Per-file TZ detection (reads `CreationDate`, `OffsetTimeOriginal`, etc).                     |
| `tk_picker.py`                    | One shared `choose_directory(title)` so every script picks folders the same way.             |

## Two things worth knowing about (the gotchas)

### Timezone handling

Image EXIF dates are stored as **local time** (no TZ info). Video QuickTime
dates are stored as **UTC** by spec. So when renaming a video using its
metadata, we have to know the photo's local TZ to recover the on-camera time.

Most photos in the master library are NZ-shot, so NZ is the fallback. But for
photos taken overseas (e.g. Melbourne, +10:00):
- The video's `CreationDate` (Apple Keys atom) carries the explicit offset.
- `photo_lib.timezone_detection.detect_file_tz` reads that offset and uses
  it to convert the UTC tags back to the right local time.
- Without this, an overseas video would always rename to NZ-shifted time.

For Google Takeout imports, the JSON's `geoData` block carries the GPS
coordinates, which `photo_lib.takeout_geo` looks up via `timezonefinder` to
derive the local TZ. Again falls back to NZ when there's no GPS.

### The Jan-1 midnight placeholder bump

Some old photos only have a year known, not a date. Their filenames look like
`2000-01-01 00.00.00_1.jpg`. If you write `2000-01-01 00:00:00` to EXIF and
then view it in a UTC-respecting viewer, NZ-local midnight Jan 1 = Dec 31 in
UTC, so the photo rolls back to 1999. To prevent that, `write_exif_from_filename.py`
rewrites the EXIF time to 13:00 NZ-equivalent (which equals 00:00 UTC exactly
during NZDT) AND renames the file to match, so the date lands cleanly on Jan 1
in every viewer and the filename ≡ EXIF invariant holds.

The audit script knows about this bump too — see
`audit_master.check_file`.

## Tests

Run them all:

```
python -m unittest discover -s tests
```

The tests are organised one-to-one with the source files (where it made sense
to test that file):

| Test file                                            | Tests                                                          |
|------------------------------------------------------|----------------------------------------------------------------|
| `tests/test_workflow.py`                             | rename + write-EXIF round trip, overseas, extensions           |
| `tests/test_change_dates_from_filename.py`           | `ChangeDatesFromFileName.py` recursive variant                 |
| `tests/test_audit_master.py`                         | `audit_master.py` per-file check + full main() run + bumps     |
| `tests/test_takeout_matching.py`                     | `UpdateFileNameToDateFromGoogleTakeoutJSONMetadata.py`         |
| `tests/test_ingest_inbox_to_master.py`               | `IngestInboxToMaster.py`                                       |
| `tests/test_remove_live_photo_videos.py`             | `RemoveLivePhotoVideos.py`                                     |
| `tests/test_bring_files_to_top_level.py`             | `BringFilesToTopLevelDirectory.py`                             |
| `tests/test_extract_and_bring.py`                    | `ExtractAndBringAllFilesToTopLevelDirectory.py`                |
| `tests/test_split_media_into_folders.py`             | `SplitMediaIntoFolders.py`                                     |
| `tests/test_detect_malformed_filenames.py`           | `DetectMalformedFileNames.py` (just the regex)                 |
| `tests/test_photo_lib/test_timezone_detection.py`    | `photo_lib/timezone_detection.py`                              |
| `tests/test_photo_lib/test_filename_pattern.py`      | `photo_lib/filename_pattern.py`                                |
| `tests/test_photo_lib/test_takeout_geo.py`           | `photo_lib/takeout_geo.py`                                     |

`tests/_fixture_helpers.py` builds cached sample image/video files with known
EXIF for the test suite to use. The fixtures are reused across runs.

## How to understand a piece of this codebase

If you want to know how X works, the recipe is roughly:

1. **Read the script's module docstring** at the top of the `.py` file. Every
   runnable script has one explaining what it does and why.
2. **Read the test file for that script** to see real usage examples — the
   test class docstrings spell out the behaviour each suite is locking in.
3. If the script imports from `photo_lib`, read the relevant `photo_lib`
   module's docstring (also at the top of each file) for the underlying
   helper's contract.

The codebase is intentionally small and flat — one library package, a handful
of scripts. There's no framework to learn.
