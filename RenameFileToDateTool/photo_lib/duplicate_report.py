"""HTML side-by-side report for duplicate review.

Renders one self-contained HTML file: each duplicate group is a row of
thumbnails with the file path, size, dimensions and tier badge underneath.
Thumbnails are inlined as base64 so the report file is portable.

Open it in a browser, scan the rows. The intended workflow is still
delete-via-File-Explorer (the report doesn't link out to your filesystem); the
report is the *quick scan* layer that File Explorer's thumbnail view does
slowly.
"""
from __future__ import annotations

import base64
import html
import io
import logging
import os
from typing import Iterable

from PIL import Image

from photo_lib.duplicate_finder import DuplicateGroup, FileFingerprint

logger = logging.getLogger("photo_lib")

THUMBNAIL_MAX_PX = 240
TIER_LABELS = {
    1: ("Tier 1", "byte-identical file", "#1f7a1f"),
    2: ("Tier 2", "identical pixels", "#1f5a7a"),
    3: ("Tier 3", "perceptually identical", "#7a3f1f"),
}


def _thumbnail_data_uri(path: str, max_px: int = THUMBNAIL_MAX_PX) -> str | None:
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((max_px, max_px))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=78)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{encoded}"
    except Exception as exc:
        logger.debug("Thumbnail failed for %s: %s", path, exc)
        return None


def _format_size(byte_count: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if byte_count < 1024:
            return f"{byte_count:.1f} {unit}" if unit != "B" else f"{byte_count} B"
        byte_count /= 1024
    return f"{byte_count:.1f} TB"


def _render_card(fingerprint: FileFingerprint, position: int, library_root: str) -> str:
    thumbnail = _thumbnail_data_uri(fingerprint.path)
    thumb_html = (
        f'<img src="{thumbnail}" />'
        if thumbnail
        else '<div class="no-thumb">no preview</div>'
    )
    relpath = os.path.relpath(fingerprint.path, library_root)
    dimensions = (
        f"{fingerprint.width}×{fingerprint.height}"
        if fingerprint.width and fingerprint.height
        else "?"
    )
    is_winner = position == 0
    badge = '<span class="winner">winner</span>' if is_winner else ""
    return (
        f'<div class="card{" winner-card" if is_winner else ""}">'
        f'  <div class="thumb">{thumb_html}</div>'
        f'  <div class="meta">'
        f'    {badge}'
        f'    <div class="path">{html.escape(relpath)}</div>'
        f'    <div class="stats">{dimensions} · {_format_size(fingerprint.size)}</div>'
        f'  </div>'
        f'</div>'
    )


def _render_group(group: DuplicateGroup, library_root: str) -> str:
    label, blurb, color = TIER_LABELS[group.tier]
    cards = "\n".join(
        _render_card(fingerprint, position, library_root)
        for position, fingerprint in enumerate(group.ranked())
    )
    return (
        f'<div class="group">'
        f'  <div class="group-header" style="border-left-color: {color}">'
        f'    <span class="tier-badge" style="background: {color}">{label}</span>'
        f'    <span class="tier-blurb">{blurb}</span>'
        f'    <span class="count">{len(group.fingerprints)} files</span>'
        f'  </div>'
        f'  <div class="cards">{cards}</div>'
        f'</div>'
    )


_HTML_HEAD = """<!doctype html>
<html><head><meta charset="utf-8"><title>Duplicate review</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       margin: 1.5rem; background: #f6f6f6; }
h1 { font-size: 1.3rem; }
.summary { background: #fff; padding: 0.75rem 1rem; border-radius: 6px;
           margin-bottom: 1rem; }
.group { background: #fff; border-radius: 6px; margin-bottom: 0.75rem;
         padding: 0.5rem 0.75rem; }
.group-header { border-left: 4px solid; padding-left: 0.6rem; margin-bottom: 0.4rem;
                display: flex; gap: 0.6rem; align-items: center;
                font-size: 0.9rem; }
.tier-badge { color: #fff; padding: 0.1rem 0.5rem; border-radius: 3px;
              font-weight: bold; font-size: 0.8rem; }
.tier-blurb { color: #555; }
.count { margin-left: auto; color: #777; font-size: 0.85rem; }
.cards { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.card { width: 260px; background: #fafafa; border-radius: 4px; padding: 0.4rem;
        border: 1px solid #e6e6e6; }
.card.winner-card { border-color: #1f7a1f; box-shadow: 0 0 0 1px #1f7a1f55; }
.thumb { width: 100%; aspect-ratio: 1 / 1; background: #ddd; border-radius: 3px;
         display: flex; align-items: center; justify-content: center;
         overflow: hidden; }
.thumb img { max-width: 100%; max-height: 100%; }
.no-thumb { color: #888; font-size: 0.8rem; }
.meta { padding-top: 0.4rem; font-size: 0.8rem; }
.path { font-family: ui-monospace, Consolas, monospace; word-break: break-all;
        color: #222; }
.stats { color: #666; margin-top: 0.15rem; }
.winner { display: inline-block; background: #1f7a1f; color: #fff;
          font-size: 0.7rem; padding: 0.05rem 0.35rem; border-radius: 3px;
          margin-bottom: 0.2rem; }
</style>
</head><body>
"""


def render_html_report(
    groups: Iterable[DuplicateGroup],
    library_root: str,
) -> str:
    """Return a complete HTML document string for the given duplicate groups."""
    materialized = list(groups)
    groups_by_tier = {1: [], 2: [], 3: []}
    for group in materialized:
        groups_by_tier.setdefault(group.tier, []).append(group)
    total_files = sum(len(group.fingerprints) for group in materialized)
    parts = [_HTML_HEAD]
    parts.append(f'<h1>Duplicate review — {os.path.basename(library_root) or library_root}</h1>')
    parts.append(
        f'<div class="summary">'
        f'  {len(materialized)} groups, {total_files} files. '
        f'  Tier 1: {len(groups_by_tier[1])}; '
        f'  Tier 2: {len(groups_by_tier[2])}; '
        f'  Tier 3: {len(groups_by_tier[3])}.'
        f'</div>'
    )
    sorted_groups = sorted(materialized, key=lambda g: (g.tier, -len(g.fingerprints)))
    for group in sorted_groups:
        parts.append(_render_group(group, library_root))
    parts.append("</body></html>")
    return "\n".join(parts)
