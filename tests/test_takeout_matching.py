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

    def test_webp_extension_recognised(self):
        # Google Photos sometimes exports WebP. The MEDIA_EXTENSIONS regex must
        # cover it or the JSON↔media match silently fails.
        self.assertEqual(
            takeout.infer_media_filename_from_json("IMG_4242.webp.supplemental-metadata.json"),
            "IMG_4242.webp",
        )


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


class TestPlanDestinationFilename(unittest.TestCase):
    """plan_destination_filename is the destination-naming logic. Always appends
    ``_N`` (matching the rest of the codebase's convention), and advances N to
    avoid both existing-on-disk files AND not-yet-written planned files in the
    current run. Critically, it also content-hashes the source against existing
    destination files with the same base timestamp — re-running the ingest must
    be idempotent, not allocate fresh _3, _4, … alongside the original _1, _2."""

    def setUp(self):
        import tempfile
        self.destination = tempfile.mkdtemp(prefix='plan_dst_')
        self.source = tempfile.mkdtemp(prefix='plan_src_')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.destination, ignore_errors=True)
        shutil.rmtree(self.source, ignore_errors=True)

    def _write_source(self, name: str, content: bytes) -> str:
        path = os.path.join(self.source, name)
        with open(path, 'wb') as handle:
            handle.write(content)
        return path

    def test_first_file_gets_suffix_1(self):
        source = self._write_source('a.jpg', b'first')
        filename, status = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".jpg", source,
            set(), takeout.build_destination_index(self.destination), {},
        )
        self.assertEqual(filename, "2026-04-09 19.52.51_1.jpg")
        self.assertEqual(status, "allocated")

    def test_collision_with_different_content_advances_counter(self):
        # An existing destination file with the same base but different content
        # is a real collision — bump the counter, do not skip.
        with open(os.path.join(self.destination, "2026-04-09 19.52.51_1.jpg"), 'wb') as f:
            f.write(b'something_else')
        source = self._write_source('a.jpg', b'fresh content')
        filename, status = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".jpg", source,
            set(), takeout.build_destination_index(self.destination), {},
        )
        self.assertEqual(filename, "2026-04-09 19.52.51_2.jpg")
        self.assertEqual(status, "allocated")

    def test_planned_names_block_reuse(self):
        # Two distinct source files with the same timestamp need distinct suffixes
        # even before either has been copied to disk yet.
        source_a = self._write_source('a.jpg', b'aaaa')
        source_b = self._write_source('b.jpg', b'bbbb')
        planned_names = set()
        planned_hashes: dict = {}
        index = takeout.build_destination_index(self.destination)
        first_name, _ = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".jpg", source_a,
            planned_names, index, planned_hashes,
        )
        second_name, _ = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".jpg", source_b,
            planned_names, index, planned_hashes,
        )
        self.assertEqual(first_name, "2026-04-09 19.52.51_1.jpg")
        self.assertEqual(second_name, "2026-04-09 19.52.51_2.jpg")

    def test_existing_destination_with_same_content_is_skipped(self):
        # The bug fix: an existing destination file with identical content must
        # be detected as a duplicate and skipped, not get a new counter.
        content = b'this is the same content'
        with open(os.path.join(self.destination, "2026-04-09 19.52.51_1.jpg"), 'wb') as f:
            f.write(content)
        source = self._write_source('different_name.jpg', content)
        filename, status = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".jpg", source,
            set(), takeout.build_destination_index(self.destination), {},
        )
        self.assertIsNone(filename)
        self.assertTrue(status.startswith("skipped_existing:"), status)
        self.assertIn("2026-04-09 19.52.51_1.jpg", status)

    def test_duplicate_within_same_run_is_skipped(self):
        # Two source files in the same run with identical content + timestamp
        # should produce one destination file, not two.
        content = b'identical bytes'
        source_a = self._write_source('a.jpg', content)
        source_b = self._write_source('b.jpg', content)
        planned_names: set = set()
        planned_hashes: dict = {}
        index = takeout.build_destination_index(self.destination)
        first_name, first_status = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".jpg", source_a,
            planned_names, index, planned_hashes,
        )
        second_name, second_status = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".jpg", source_b,
            planned_names, index, planned_hashes,
        )
        self.assertEqual(first_status, "allocated")
        self.assertEqual(first_name, "2026-04-09 19.52.51_1.jpg")
        self.assertIsNone(second_name)
        self.assertTrue(second_status.startswith("skipped_planned:"), second_status)
        self.assertIn("2026-04-09 19.52.51_1.jpg", second_status)

    def test_jpeg_extension_canonicalized_to_jpg(self):
        # A source ``.jpeg`` lands at the destination as ``.jpg`` — the ingest
        # consolidates spelling variants so the destination is never mixed.
        source = self._write_source('IMG_0001.jpeg', b'jpeg-bytes')
        filename, status = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".jpeg", source,
            set(), takeout.build_destination_index(self.destination), {},
        )
        self.assertEqual(filename, "2026-04-09 19.52.51_1.jpg")
        self.assertEqual(status, "allocated")

    def test_uppercase_extension_canonicalized_to_lowercase(self):
        source = self._write_source('IMG_0001.JPG', b'jpg-bytes')
        filename, _status = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".JPG", source,
            set(), takeout.build_destination_index(self.destination), {},
        )
        self.assertEqual(filename, "2026-04-09 19.52.51_1.jpg")

    def test_counter_is_global_across_extensions_at_same_base(self):
        # A .jpg already occupying _1 must block a fresh .mp4 from taking _1 —
        # the .mp4 lands at _2.mp4. Pre-fix the counter was per-extension and
        # both could share _1 at the same timestamp.
        with open(os.path.join(self.destination, "2026-04-09 19.52.51_1.jpg"), 'wb') as f:
            f.write(b'an_existing_jpg')
        source = self._write_source('video.mp4', b'an_mp4_video')
        filename, status = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".mp4", source,
            set(), takeout.build_destination_index(self.destination), {},
        )
        self.assertEqual(filename, "2026-04-09 19.52.51_2.mp4")
        self.assertEqual(status, "allocated")

    def test_global_counter_works_against_jpeg_already_present(self):
        # An existing .jpeg in dest (legacy) occupying _1 should block a fresh
        # .mp4 from claiming _1 — the canonical-extension key catches it.
        with open(os.path.join(self.destination, "2026-04-09 19.52.51_1.jpeg"), 'wb') as f:
            f.write(b'legacy_jpeg_in_dest')
        source = self._write_source('video.mp4', b'fresh_mp4')
        filename, _status = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".mp4", source,
            set(), takeout.build_destination_index(self.destination), {},
        )
        self.assertEqual(filename, "2026-04-09 19.52.51_2.mp4")

    def test_planned_jpeg_then_jpg_counter_advances(self):
        # First a .jpeg is planned (canonicalizes to _1.jpg). A second source
        # file with a real .jpg extension must NOT collide; it gets _2.jpg.
        source_a = self._write_source('a.jpeg', b'aaaa')
        source_b = self._write_source('b.jpg', b'bbbb')
        planned_names: set = set()
        planned_hashes: dict = {}
        index = takeout.build_destination_index(self.destination)
        first_name, _ = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".jpeg", source_a,
            planned_names, index, planned_hashes,
        )
        second_name, _ = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".jpg", source_b,
            planned_names, index, planned_hashes,
        )
        self.assertEqual(first_name, "2026-04-09 19.52.51_1.jpg")
        self.assertEqual(second_name, "2026-04-09 19.52.51_2.jpg")

    def test_jpeg_dedup_against_jpg_with_same_content(self):
        # If the destination already has _1.jpg with identical content, an
        # incoming .jpeg with the same bytes must be detected as a duplicate
        # (canonical-ext key catches it) and skipped.
        content = b'identical-pixels-different-spelling'
        with open(os.path.join(self.destination, "2026-04-09 19.52.51_1.jpg"), 'wb') as f:
            f.write(content)
        source = self._write_source('IMG.jpeg', content)
        filename, status = takeout.plan_destination_filename(
            self.destination, "2026-04-09 19.52.51", ".jpeg", source,
            set(), takeout.build_destination_index(self.destination), {},
        )
        self.assertIsNone(filename)
        self.assertTrue(status.startswith("skipped_existing:"), status)


class TestBuildUsedIndicesByBase(unittest.TestCase):
    """Verifies the helper that pre-populates _N usage from an existing dest."""

    def setUp(self):
        import tempfile
        self.destination = tempfile.mkdtemp(prefix='used_idx_')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.destination, ignore_errors=True)

    def _touch(self, name: str) -> None:
        with open(os.path.join(self.destination, name), 'wb') as f:
            f.write(b'x')

    def test_empty_destination_returns_empty(self):
        self.assertEqual(takeout.build_used_indices_by_base(self.destination), {})

    def test_collects_indices_across_extensions_per_base(self):
        self._touch("2026-04-09 19.52.51_1.jpg")
        self._touch("2026-04-09 19.52.51_2.mp4")
        self._touch("2026-04-09 19.52.51_5.heic")
        self._touch("2026-04-10 12.00.00_1.jpg")
        used = takeout.build_used_indices_by_base(self.destination)
        self.assertEqual(used, {
            "2026-04-09 19.52.51": {1, 2, 5},
            "2026-04-10 12.00.00": {1},
        })

    def test_skips_non_canonical_files(self):
        self._touch("random_file.jpg")
        self._touch("2026-04-09 19.52.51_1.jpg")
        used = takeout.build_used_indices_by_base(self.destination)
        self.assertEqual(used, {"2026-04-09 19.52.51": {1}})


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
        # The orphan MP4 inherits the HEIC's timestamp, but the _N counter is
        # global across extensions at this base — so the .heic takes _1 and
        # the .mp4 bumps to _2. (Pre-fix the counter was per-extension and
        # both could share _1, which made it ambiguous whether they were a
        # Live Photo pair or two unrelated files at the same instant.)
        self.assertEqual(produced, [
            '2026-04-09 21.52.51_1.heic',
            '2026-04-09 21.52.51_2.mp4',
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

    def test_multiple_source_folders_into_one_destination(self):
        # Two source folders, each with its own JSON+media. Their content is distinct
        # and their timestamps differ — should produce two destination files.
        import json as _json, tempfile, shutil
        extra_source = tempfile.mkdtemp(prefix='takeout_extra_src_')
        try:
            with open(os.path.join(self.source, 'IMG_AAA.JPG'), 'wb') as media_file:
                media_file.write(b'aaaa_bytes')
            with open(os.path.join(self.source, 'IMG_AAA.JPG.json'), 'w') as json_file:
                _json.dump({"photoTakenTime": {"timestamp": "1775728371"}}, json_file)
            with open(os.path.join(extra_source, 'IMG_BBB.JPG'), 'wb') as media_file:
                media_file.write(b'bbbb_bytes_distinct')
            with open(os.path.join(extra_source, 'IMG_BBB.JPG.json'), 'w') as json_file:
                # 1775728381 = 10 seconds later → distinct timestamp
                _json.dump({"photoTakenTime": {"timestamp": "1775728381"}}, json_file)

            takeout.process_and_copy_media_files(
                [self.source, extra_source], self.destination, dry_run=False,
            )

            produced = sorted(
                f for f in os.listdir(self.destination) if f.lower().endswith('.jpg')
            )
            self.assertEqual(produced, [
                '2026-04-09 21.52.51_1.jpg',
                '2026-04-09 21.53.01_1.jpg',
            ])
        finally:
            shutil.rmtree(extra_source, ignore_errors=True)

    def test_multiple_source_folders_dedup_across_sources(self):
        # Two source folders, same timestamp, identical content — should produce ONE file.
        import json as _json, tempfile, shutil
        extra_source = tempfile.mkdtemp(prefix='takeout_extra_src_')
        try:
            identical_bytes = b'these bytes appear in both sources'
            with open(os.path.join(self.source, 'IMG_A.JPG'), 'wb') as media_file:
                media_file.write(identical_bytes)
            with open(os.path.join(self.source, 'IMG_A.JPG.json'), 'w') as json_file:
                _json.dump({"photoTakenTime": {"timestamp": "1775728371"}}, json_file)
            with open(os.path.join(extra_source, 'IMG_B.JPG'), 'wb') as media_file:
                media_file.write(identical_bytes)
            with open(os.path.join(extra_source, 'IMG_B.JPG.json'), 'w') as json_file:
                _json.dump({"photoTakenTime": {"timestamp": "1775728371"}}, json_file)

            takeout.process_and_copy_media_files(
                [self.source, extra_source], self.destination, dry_run=False,
            )

            produced = sorted(
                f for f in os.listdir(self.destination) if f.lower().endswith('.jpg')
            )
            self.assertEqual(produced, ['2026-04-09 21.52.51_1.jpg'])
        finally:
            shutil.rmtree(extra_source, ignore_errors=True)

    def test_rerunning_does_not_duplicate_files(self):
        # Reproduces the bug: running the script twice on the same source folder
        # used to leave _1, _2 from the first run plus _3, _4 from the second run.
        # With content-aware dedup it should leave exactly the original _1, _2.
        self._seed('IMG_A.JPG', {"photoTakenTime": {"timestamp": "1775728371"}})
        self._seed('IMG_B.JPG', {"photoTakenTime": {"timestamp": "1775728371"}})
        # Force a true content difference between the two photos, since _seed
        # writes identical bytes by default and dedup would collapse them.
        with open(os.path.join(self.source, 'IMG_B.JPG'), 'wb') as handle:
            handle.write(b'different media bytes')

        takeout.process_and_copy_media_files(self.source, self.destination, dry_run=False)
        takeout.process_and_copy_media_files(self.source, self.destination, dry_run=False)

        produced_media = sorted(
            f for f in os.listdir(self.destination) if f.lower().endswith('.jpg')
        )
        self.assertEqual(produced_media, [
            '2026-04-09 21.52.51_1.jpg',
            '2026-04-09 21.52.51_2.jpg',
        ])


if __name__ == '__main__':
    unittest.main()
