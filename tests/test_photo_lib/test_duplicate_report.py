"""Smoke tests for photo_lib.duplicate_report: HTML structure + escaping."""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, 'RenameFileToDateTool'))

from photo_lib.duplicate_finder import DuplicateGroup, FileFingerprint  # noqa: E402
from photo_lib.duplicate_report import render_html_report  # noqa: E402


def _fp(path: str, size: int = 1000) -> FileFingerprint:
    return FileFingerprint(
        path=path, size=size, mtime=0.0, media_kind="image",
        file_sha256="x", pixel_sha256="y", phash_hex="z",
        frame_phashes_hex=None, width=100, height=100,
    )


class TestRenderHtmlReport(unittest.TestCase):
    def test_empty_report_still_well_formed(self):
        html_text = render_html_report([], "/lib")
        self.assertIn("<html>", html_text)
        self.assertIn("</html>", html_text)
        self.assertIn("0 groups", html_text)

    def test_group_summary_counts_groups_by_tier(self):
        groups = [
            DuplicateGroup(tier=1, fingerprints=[_fp("/lib/a.jpg"), _fp("/lib/b.jpg")]),
            DuplicateGroup(tier=2, fingerprints=[_fp("/lib/c.jpg"), _fp("/lib/d.jpg")]),
            DuplicateGroup(tier=2, fingerprints=[_fp("/lib/e.jpg"), _fp("/lib/f.jpg")]),
            DuplicateGroup(tier=3, fingerprints=[_fp("/lib/g.jpg"), _fp("/lib/h.jpg")]),
        ]
        html_text = render_html_report(groups, "/lib")
        self.assertIn("4 groups", html_text)
        self.assertIn("Tier 1: 1;", html_text)
        self.assertIn("Tier 2: 2;", html_text)
        self.assertIn("Tier 3: 1.", html_text)

    def test_path_is_escaped(self):
        # A path with HTML-special chars must not inject markup.
        groups = [
            DuplicateGroup(
                tier=1,
                fingerprints=[_fp("/lib/<script>alert.jpg"), _fp("/lib/b.jpg")],
            )
        ]
        html_text = render_html_report(groups, "/lib")
        self.assertNotIn("<script>alert.jpg", html_text)
        self.assertIn("&lt;script&gt;alert.jpg", html_text)


if __name__ == "__main__":
    unittest.main()
