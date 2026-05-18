"""Tests for photo_lib.takeout_geo: GPS → timezone resolution for Takeout JSON.

Without this, every overseas-shot photo from a Takeout dump would land in the master
library with a NZ-shifted filename, off by 2-13 hours from the camera's recorded time.
"""

import os
import sys
import unittest
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

from photo_lib import takeout_geo  # noqa: E402

# UTC instant 2026-04-09 09:52:51Z. Local times at that instant:
#   Melbourne (AEST +10:00): 19:52:51 — April is post-DST in AU
#   NZ (NZST +12:00):        21:52:51 — April is post-DST in NZ
#   Paris (CEST +02:00):     11:52:51 — Europe is still on DST in April
SAMPLE_UTC_TIMESTAMP = "1775728371"


def _make_metadata(latitude, longitude, *, prefer_exif=True):
    """Build a minimal Takeout-style JSON dict with GPS in either block."""
    metadata = {"photoTakenTime": {"timestamp": SAMPLE_UTC_TIMESTAMP}}
    geo = {"latitude": latitude, "longitude": longitude, "altitude": 0.0}
    if prefer_exif:
        metadata["geoDataExif"] = geo
        metadata["geoData"] = {"latitude": 0.0, "longitude": 0.0, "altitude": 0.0}
    else:
        metadata["geoData"] = geo
        metadata["geoDataExif"] = {"latitude": 0.0, "longitude": 0.0, "altitude": 0.0}
    return metadata


class TestResolveTimezoneFromGeo(unittest.TestCase):
    """resolve_timezone_from_geo looks up the photo's TZ from GPS coords, preferring
    geoDataExif (camera EXIF) over geoData (Google-enriched/inferred). Treats
    ``(0.0, 0.0)`` as Google's no-GPS-data sentinel rather than the Atlantic Ocean."""

    def test_melbourne_coords_resolve_to_melbourne(self):
        tz = takeout_geo.resolve_timezone_from_geo(_make_metadata(-37.81, 144.96))
        self.assertEqual(tz, ZoneInfo("Australia/Melbourne"))

    def test_nz_coords_resolve_to_auckland(self):
        tz = takeout_geo.resolve_timezone_from_geo(_make_metadata(-36.85, 174.76))
        self.assertEqual(tz, ZoneInfo("Pacific/Auckland"))

    def test_paris_coords_resolve_to_paris(self):
        tz = takeout_geo.resolve_timezone_from_geo(_make_metadata(48.85, 2.35))
        self.assertEqual(tz, ZoneInfo("Europe/Paris"))

    def test_zero_zero_treated_as_no_gps(self):
        # Google writes (0,0) when no GPS data — must NOT be treated as Atlantic Ocean.
        self.assertIsNone(takeout_geo.resolve_timezone_from_geo(
            _make_metadata(0.0, 0.0)
        ))

    def test_missing_geo_blocks_returns_none(self):
        self.assertIsNone(takeout_geo.resolve_timezone_from_geo(
            {"photoTakenTime": {"timestamp": SAMPLE_UTC_TIMESTAMP}}
        ))

    def test_prefers_exif_block_over_enriched(self):
        # geoDataExif = Melbourne, geoData = Paris → expect Melbourne (the EXIF wins)
        metadata = {
            "photoTakenTime": {"timestamp": SAMPLE_UTC_TIMESTAMP},
            "geoDataExif": {"latitude": -37.81, "longitude": 144.96},
            "geoData":     {"latitude": 48.85, "longitude": 2.35},
        }
        tz = takeout_geo.resolve_timezone_from_geo(metadata)
        self.assertEqual(tz, ZoneInfo("Australia/Melbourne"))

    def test_falls_back_to_geo_when_exif_is_zero(self):
        # Common case: EXIF has no GPS but Google enriched it.
        metadata = {
            "photoTakenTime": {"timestamp": SAMPLE_UTC_TIMESTAMP},
            "geoDataExif": {"latitude": 0.0, "longitude": 0.0},
            "geoData":     {"latitude": -37.81, "longitude": 144.96},
        }
        tz = takeout_geo.resolve_timezone_from_geo(metadata)
        self.assertEqual(tz, ZoneInfo("Australia/Melbourne"))


class TestLocalDatetimeFromMetadata(unittest.TestCase):
    """local_datetime_from_metadata turns Takeout's UTC photoTakenTime into a local-
    time wallclock at the photo's GPS location, falling back to NZ when no GPS."""

    def test_melbourne_photo_uses_melbourne_local_time(self):
        local_dt = takeout_geo.local_datetime_from_metadata(_make_metadata(-37.81, 144.96))
        self.assertEqual(local_dt.strftime("%Y-%m-%d %H.%M.%S"), "2026-04-09 19.52.51")

    def test_paris_photo_uses_paris_local_time(self):
        # April → Europe/Paris is CEST (UTC+2). 09:52:51 UTC → 11:52:51 local.
        local_dt = takeout_geo.local_datetime_from_metadata(_make_metadata(48.85, 2.35))
        self.assertEqual(local_dt.strftime("%Y-%m-%d %H.%M.%S"), "2026-04-09 11.52.51")

    def test_no_gps_falls_back_to_nz(self):
        # No GPS → NZ. April in NZ is NZST (UTC+12). 09:52:51 UTC → 21:52:51 NZ.
        local_dt = takeout_geo.local_datetime_from_metadata(
            {"photoTakenTime": {"timestamp": SAMPLE_UTC_TIMESTAMP}}
        )
        self.assertEqual(local_dt.strftime("%Y-%m-%d %H.%M.%S"), "2026-04-09 21.52.51")

    def test_zero_zero_gps_falls_back_to_nz(self):
        local_dt = takeout_geo.local_datetime_from_metadata(_make_metadata(0.0, 0.0))
        self.assertEqual(local_dt.strftime("%Y-%m-%d %H.%M.%S"), "2026-04-09 21.52.51")


if __name__ == '__main__':
    unittest.main()
