"""Tests for update_filename_to_date_from_google_takeout_json_metadata.py — the script that
ingests a Google Takeout dump and copies each media file to the destination with a
canonical YYYY-MM-DD HH.MM.SS_N.ext filename based on the JSON's photoTakenTime.

Covers:
  - JSON↔media filename matching (infer_media_filename_from_json, find_matching_media_for_json)
  - Live-Photo pairing fallback (orphan MP4 inherits HEIC companion's timestamp)
  - Unique-filename generation under collisions
  - End-to-end with GPS-derived timezone (overseas-photo path)

The TZ-resolution helpers themselves live in photo_lib.takeout_geo and are unit-tested
separately in tests/test_photo_lib/test_takeout_geo.py.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

import update_filename_to_date_from_google_takeout_json_metadata as takeout  # noqa: E402


class TestInferMediaFilenameFromJson(unittest.TestCase):
    """infer_media_filename_from_json is the FIRST matching attempt: take a JSON
    filename and try to reconstruct the media filename it should describe. Handles
    the various Takeout-format quirks — plain ``.json`` suffix, the newer
    ``.supplemental-metadata.json`` suffix, duplicate-suffix repositioning (Takeout
    puts ``(1)`` after the extension in the JSON name but before it in the media)."""

    def test_plain_json(self):
        # "IMG_1234.JPG.json" → "IMG_1234.JPG"
        self.assertEqual(
            takeout.infer_media_filename_from_json("IMG_1234.JPG.json"),
            "IMG_1234.JPG",
        )

    def test_supplemental_metadata_suffix(self):
        # Recent Takeout exports use ".supplemental-metadata.json"
        self.assertEqual(
            takeout.infer_media_filename_from_json("IMG_1234.HEIC.supplemental-metadata.json"),
            "IMG_1234.HEIC",
        )

    def test_duplicate_paren_suffix_repositioned(self):
        # "IMG_1234.JPG(1).json" → "IMG_1234(1).JPG"
        # Takeout puts the dup token after the extension in the JSON name, but the
        # actual media file has it before the extension.
        self.assertEqual(
            takeout.infer_media_filename_from_json("IMG_1234.JPG(1).json"),
            "IMG_1234(1).JPG",
        )

    def test_duplicate_underscore_suffix_repositioned(self):
        self.assertEqual(
            takeout.infer_media_filename_from_json("IMG_1234.JPG_1.json"),
            "IMG_1234_1.JPG",
        )

    def test_no_recognizable_extension_returns_none(self):
        # No media extension anywhere in the stem
        self.assertIsNone(takeout.infer_media_filename_from_json("somefile.json"))

    def test_non_json_filename_returns_none(self):
        self.assertIsNone(takeout.infer_media_filename_from_json("IMG_1234.JPG"))

    def test_uppercase_extension_preserved(self):
        # The matcher must not lowercase the extension, since on case-sensitive systems
        # it'd no longer match the actual file.
        result = takeout.infer_media_filename_from_json("video.MOV.json")
        self.assertEqual(result, "video.MOV")


class TestFindMatchingMedia(unittest.TestCase):
    """find_matching_media_for_json is the orchestrator: try the inferred-name
    match first, then fall back to a stem-prefix match (handles truncated names),
    then to a longest-common-prefix fuzzy match. Returns (matched_name, method)
    so the script can log which path matched."""

    def _make_lookup(self, *media_names):
        media_lower_map = {f.lower(): f for f in media_names}
        sorted_lower = sorted(media_lower_map.keys())
        return media_lower_map, sorted_lower

    def test_exact_inferred_match(self):
        media_lower_map, sorted_lower = self._make_lookup("IMG_1234.JPG")
        match, method = takeout.find_matching_media_for_json(
            "IMG_1234.JPG.json", media_lower_map, sorted_lower
        )
        self.assertEqual(match, "IMG_1234.JPG")
        self.assertEqual(method, "exact_inferred")

    def test_startswith_fallback_for_truncated_name(self):
        # Takeout sometimes truncates long stems in the JSON name (~46 chars). The
        # actual file's stem is longer. Stem fallback should pick it up.
        media_lower_map, sorted_lower = self._make_lookup(
            "very_long_descriptive_filename_taken_in_2026.JPG"
        )
        match, method = takeout.find_matching_media_for_json(
            "very_long_descriptive_filename_taken_in_2026.json",  # no media ext
            media_lower_map, sorted_lower,
        )
        self.assertEqual(match, "very_long_descriptive_filename_taken_in_2026.JPG")
        self.assertEqual(method, "startswith_fallback")

    def test_no_match_returns_none(self):
        media_lower_map, sorted_lower = self._make_lookup("IMG_1234.JPG")
        match, method = takeout.find_matching_media_for_json(
            "totally_different.json", media_lower_map, sorted_lower
        )
        self.assertIsNone(match)
        self.assertEqual(method, "no_match")

    def test_supplemental_metadata_match(self):
        media_lower_map, sorted_lower = self._make_lookup("IMG_5678.HEIC")
        match, method = takeout.find_matching_media_for_json(
            "IMG_5678.HEIC.supplemental-metadata.json", media_lower_map, sorted_lower
        )
        self.assertEqual(match, "IMG_5678.HEIC")
        self.assertEqual(method, "exact_inferred")

    def test_case_insensitive_match(self):
        # JSON has lowercase ext, media file has uppercase
        media_lower_map, sorted_lower = self._make_lookup("IMG_999.MOV")
        match, _method = takeout.find_matching_media_for_json(
            "IMG_999.mov.json", media_lower_map, sorted_lower
        )
        self.assertEqual(match, "IMG_999.MOV")


class TestGenerateUniqueFilename(unittest.TestCase):
    """generate_unique_filename is the destination-naming logic. Always appends
    ``_N`` (matching the rest of the codebase's convention), and advances N to
    avoid both existing-on-disk files AND not-yet-written planned files in the
    current run (so two photos with identical timestamps get _1 and _2 even
    before the first copy actually completes)."""

    def test_first_file_gets_suffix_1(self):
        # Even when no collision, the script always appends _N — see docstring on
        # generate_unique_filename. This matches main.py's rename convention.
        import tempfile, shutil
        tmp = tempfile.mkdtemp()
        try:
            result = takeout.generate_unique_filename(tmp, "2026-04-09 19.52.51", ".jpg", set())
            self.assertEqual(result, "2026-04-09 19.52.51_1.jpg")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_collision_with_existing_file_advances_counter(self):
        import tempfile, shutil
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "2026-04-09 19.52.51_1.jpg"), "w") as f:
                f.write("")
            result = takeout.generate_unique_filename(tmp, "2026-04-09 19.52.51", ".jpg", set())
            self.assertEqual(result, "2026-04-09 19.52.51_2.jpg")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_planned_names_block_reuse(self):
        # Two media files with the same timestamp need distinct suffixes even before
        # either has been copied to disk yet.
        import tempfile, shutil
        tmp = tempfile.mkdtemp()
        try:
            planned = set()
            a = takeout.generate_unique_filename(tmp, "2026-04-09 19.52.51", ".jpg", planned)
            b = takeout.generate_unique_filename(tmp, "2026-04-09 19.52.51", ".jpg", planned)
            self.assertEqual(a, "2026-04-09 19.52.51_1.jpg")
            self.assertEqual(b, "2026-04-09 19.52.51_2.jpg")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestLivePhotoPairingFallback(unittest.TestCase):
    """A Takeout dump often has IMG_3118.HEIC + IMG_3118.HEIC.supplemental-metadata.json
    + IMG_3118.MP4 (the Live Photo video), but no separate JSON for the MP4. The script
    has a second pass that gives the orphaned MP4 the same timestamp as its companion."""

    def setUp(self):
        import tempfile
        self.source = tempfile.mkdtemp(prefix='takeout_pair_src_')
        self.destination = tempfile.mkdtemp(prefix='takeout_pair_dst_')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.source, ignore_errors=True)
        shutil.rmtree(self.destination, ignore_errors=True)

    def _seed_file(self, name, content=b'x'):
        with open(os.path.join(self.source, name), 'wb') as f:
            f.write(content)

    def _seed_json(self, name, metadata):
        import json
        with open(os.path.join(self.source, name), 'w') as f:
            json.dump(metadata, f)

    def test_orphan_mp4_inherits_companion_heic_timestamp(self):
        # 1775728371 = 2026-04-09 09:52:51 UTC = 21:52:51 NZ (the fallback when no GPS)
        metadata = {"photoTakenTime": {"timestamp": "1775728371"}}
        self._seed_file('IMG_3118.HEIC')
        self._seed_file('IMG_3118.MP4')  # no JSON for the MP4
        self._seed_json('IMG_3118.HEIC.supplemental-metadata.json', metadata)

        takeout.process_and_copy_media_files(self.source, self.destination, dry_run=False)

        produced = sorted(f for f in os.listdir(self.destination) if not f.endswith('.json') and not f.endswith('.txt'))
        # Both files should land with the same base timestamp, different extensions
        self.assertEqual(produced, [
            '2026-04-09 21.52.51_1.heic',
            '2026-04-09 21.52.51_1.mp4',
        ])


class TestEndToEndCopyWithGeo(unittest.TestCase):
    """Drives process_and_copy_media_files with a tiny fixture Takeout dump and
    checks the produced filename reflects the GPS-derived TZ — overseas photos
    should land in the destination as their *local* time, not NZ-shifted."""

    def setUp(self):
        import tempfile
        self.source = tempfile.mkdtemp(prefix='takeout_src_')
        self.destination = tempfile.mkdtemp(prefix='takeout_dst_')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.source, ignore_errors=True)
        shutil.rmtree(self.destination, ignore_errors=True)

    def _seed(self, media_filename, metadata):
        import json
        # Need a real file (any bytes) and its companion JSON in the source folder.
        with open(os.path.join(self.source, media_filename), 'wb') as media_file:
            media_file.write(b'fake media bytes')
        with open(os.path.join(self.source, f'{media_filename}.json'), 'w') as json_file:
            json.dump(metadata, json_file)

    def _metadata_with_gps(self, latitude, longitude):
        return {
            "photoTakenTime": {"timestamp": "1775728371"},  # 2026-04-09 09:52:51 UTC
            "geoDataExif": {"latitude": latitude, "longitude": longitude, "altitude": 0.0},
            "geoData": {"latitude": 0.0, "longitude": 0.0, "altitude": 0.0},
        }

    def test_melbourne_photo_renamed_to_melbourne_local_time(self):
        # Melbourne GPS, April → AEST (+10:00) → 09:52 UTC + 10h = 19:52 local
        self._seed('IMG_1234.JPG', self._metadata_with_gps(-37.81, 144.96))
        takeout.process_and_copy_media_files(self.source, self.destination, dry_run=False)
        produced = [f for f in os.listdir(self.destination) if f.lower().endswith('.jpg')]
        self.assertEqual(produced, ['2026-04-09 19.52.51_1.jpg'])

    def test_no_gps_photo_renamed_to_nz_local_time(self):
        # No GPS → NZ fallback. April → NZST (+12:00) → 09:52 UTC + 12h = 21:52 local
        self._seed('IMG_5678.JPG', {"photoTakenTime": {"timestamp": "1775728371"}})
        takeout.process_and_copy_media_files(self.source, self.destination, dry_run=False)
        produced = [f for f in os.listdir(self.destination) if f.lower().endswith('.jpg')]
        self.assertEqual(produced, ['2026-04-09 21.52.51_1.jpg'])


if __name__ == '__main__':
    unittest.main()
