# Session notes — 2026-05-19 → 2026-05-20

## Goal
Reconcile `F:\Photos` (Google Takeout dump, restructured into year folders) against the master library at `D:\Files\Pictures and Videos`. Master has roughly equal totals but per-year counts diverge, and we want to understand why.

## Key paths
- Working copy: `F:\PhotosCopy`
- Backup: `F:\Photos`
- Master: `D:\Files\Pictures and Videos`
- Logs: `D:\Files\Documents\Code\RenameFileToDateTool\RenameFileToDateTool\logs\`
  - `write_exif_from_filename.log` — exhaustive EXIF rewrite history
  - `audit_master.log` — last audit
  - `compare_libraries.log` — last compare

---

## 2026-05-19 — Working-copy setup and structural cleanup

### Working copy
- `F:\PhotosCopy` is a duplicate of `F:\Photos` (verified by `compare_libraries.py` — 15098 ↔ 15098, all `same_name`, 0 deltas).
- Renamed `F:\PhotosCopy\2000-2010` → `F:\PhotosCopy\2000 - 2010` so the folder name matches master's `BUNDLED_EARLY_FOLDER`.

### Code changes
| File | Change |
|---|---|
| `compare_libraries.py` | Added per-year breakdown table with categorized unmatched files (`same_timestamp_extra/missing`, `same_date_extra/missing`, `isolated_extra/missing`). Added `transcodes_missing_soft_delete` finding. Fixed 1-to-1 pairing bug: `find_match` now respects `consumed_after` so a second backup file can't false-pair to an already-claimed master file. |
| `audit_master.py` | Added `--root` CLI flag so the audit can target a working copy without editing `photo_lib/config.py`. |
| `photo_lib/exiftool_runner.py` | Chunked `write_exif_dates_batch` — `WRITE_BATCH_CHUNK_SIZE=100`, `WRITE_BATCH_TIMEOUT_SECONDS=1200`. The unchunked version hit the 600s subprocess timeout on the 12689-image and 1383-video batches. |
| `photo_lib/extensions.py` | Added `webp` to `IMAGE_EXTENSIONS`. |
| `update_filename_to_date_from_google_takeout_json_metadata.py` | (earlier today) Hash-based dedup in `plan_destination_filename`, multi-source support via `nargs="+"`. |

All changes covered by tests; full suite: 186 passing.

### Data work on `F:\PhotosCopy`
File count throughout: **15098** (unchanged).

| Step | Result |
|---|---|
| Audit (`audit_master.py --root F:\PhotosCopy`) | 17 folders [NEEDS FIX] (filesystem timestamps reset by copy), 720 extension mismatches, 10 wrong-year-folder files |
| `write_exif_from_filename.py --path F:\PhotosCopy` (3 attempts, last one chunked) | 15076 files updated; 14 skipped in 2011 (placeholder collision); 22 non-media files left alone (.json / .txt / .3gp / .52_1 / .webp + .gif etc.) |
| Renamed `.heif` → `.heic` | 539 files |
| Renamed PNG-as-JPEG → `.jpg` | 7 files (5 + 2 from successive exiftool warnings) |
| Moved 10 wrong-year-folder files | `2021/2022-*` → `2022/`, `2024/2025-*` → `2025/`, `2025/2026-*` → `2026/` |
| **Post-fix audit** | 14 folders [OK], 3 [NEEDS FIX] (2011, 2025, 2026); 174 ext mismatches; 0 wrong-year files |
| Comprehensive extension-mismatch rename | 170 files: `png→jpg` (73), `mov→mp4` (72), `heic→jpg` (20), `png→heic` (2), `jpeg→heic` (1), `webp→jpg` (1), `jpeg→png` (1). Skipped 3 `heic→heif` (master uses `.heic` exclusively for HEIF-family content). |

### End-of-day state
- File count: 15098 ✓
- All structural fixes done EXCEPT the 14 placeholder collisions in `F:\PhotosCopy\2011\`
- **170 files were renamed AFTER the last EXIF rewrite** — their EXIF tags were never (or only partially) written because exiftool refused them under their wrong extensions. They need another EXIF rewrite pass.

---

## 2026-05-20 — Final reconciliation pass

### 1. EXIF writer re-run (dry-run + live)
- Dry-run: only **100 files** needed EXIF writes (not the predicted 170 — many of those files already had correct EXIF baked in from earlier; exiftool had simply refused them under their wrong extensions). 14 placeholder collisions skipped, 14976 already in sync.
- Live run: 100 files updated cleanly. Chunked batches finished without timeout.

### 2. Audit re-run
- 16 folders [OK], 1 [NEEDS FIX] (2011, expected — placeholder collision)
- 4 extension flags:
  - 3 × `.heic` files claiming to be `.heif` (intentional — yesterday's convention: master uses `.heic` for all HEIF-family content)
  - 1 × `2026/2026-04-04 19.24.52_1` — extensionless file, header `ftypqt  ` confirmed QuickTime/MOV
- 0 wrong-year files, 0 non-canonical filenames

### 3. Extensionless MOV fixed
- Renamed `F:\PhotosCopy\2026\2026-04-04 19.24.52_1` → `2026-04-04 19.24.52_1.mov` (slot was free; another `.heic` shares the base name as a Live Photo sibling).
- Ran EXIF writer again — 1 file updated, 15076 in sync.

### 4. 2011 placeholder collisions resolved

**Background** (the underlying mechanic):
- `apply_placeholder_time_bump` bumps midnight Jan 1 (`YYYY-01-01 00.00.00`) to `13.00.00` because midnight NZ = Dec 31 in UTC, which rolls UTC-respecting viewers back a year. 13:00 NZ NZDT (UTC+13) = 00:00 UTC.
- `maybe_rename_placeholder` does a literal `00.00.00` → `13.00.00` swap that keeps the `_N` index. If the target `_N` slot is taken, it returns None and the EXIF writer skips the file to preserve the filename ≡ EXIF invariant.
- 2011 has **44 photos** whose source metadata gave a date but no usable time (Google Takeout `photoTakenTime` absent / epoch-zero). 30 had already been through the bump in a prior run (sitting at `13.00.00_1..30.jpg`); 14 were still at `00.00.00_1..14.jpg` and collided on rename.

**Resolution**:
- Wrote `scratch_dedup_2011.py` to pixel-hash all 14 placeholders + 31 `13.00.00_*.jpg/jpeg` files. Result: **all 44 photos are pixel-distinct**. No content duplicates. (The session-notes-19 suspicion that `00.00.00_1.jpg` and `13.00.00_1.jpeg` were the same photo pre/post EXIF was wrong.)
- Renamed `00.00.00_N.jpg` → `13.00.00_(N+30).jpg` (numeric sort, so `_1→_31`, `_2→_32`, ..., `_14→_44`).
- Ran EXIF writer — all 14 stamped cleanly with `2011:01:01 13:00:00`. 15077 in sync.

### 5. compare_libraries.py vs master

Final counts:
- BEFORE (`F:\PhotosCopy`): **15,098**
- AFTER (master): **14,958**
- Net delta: **−140**

Match types:
- Matched: 11,638 (same_name 6,179; canonical_rename 574; ext_change 64; jpeg_to_jpg 2,557; size_tiebreak 2,264)
- **0 placeholder_renames** — names align between takeout and master
- 0 transcodes

Unmatched:
- B-only: 3,460 (in takeout, not in master) — 181 same-ts, 3,094 same-date, **185 isolated**
- A-only: 3,320 (in master, not in takeout) — 377 same-ts, 2,767 same-date, **176 isolated**

Most unmatched files pair by *date* to a counterpart on the other side — sibling photos with timestamp drift (likely from successive EXIF rewrites shifting times by seconds, or HEIC+MOV Live Photo pairs where the times differ by milliseconds). The genuine candidates for "in one library, truly not in the other" are the **isolated** counts:
- **185 isolated_missing** (takeout has, master might be missing) — concentrated in 2023 (36), 2024 (30), 2008 (18), 2021 (18), 2014 mixed, 2020 (13)
- **176 isolated_extra** (master has, takeout doesn't) — concentrated in 2007 (38), 2020 (30), 2023 (13). Likely post-takeout iPhone additions.

Per-year highlights:
- **2011: clean** — 48=48, all matched. Today's placeholder rename did its job.
- **2026: −510** (largest gap). 94 same-ts, 463 same-date, 8 isolated. Takeout was generated mid-year 2026, so master and takeout have shifted timestamps on the same photos; only ~8 are real candidates for loss.
- **2015: +1 net, but 364 B-only / 365 A-only** — almost pure timestamp drift across siblings.
- **2007, 2023, 2014, 2013**: each has +40 to +90 deltas with most unmatched files pairing on date.

### 6. Size anomalies

`compare_libraries.py` flagged 406 files with >100 KB diff under the same name. Pattern in `2000 - 2010\2000-01-01 13.00.00_*.jpg` is striking: master is usually **much larger** than takeout (e.g., `_103.jpg` 140 KB → 766 KB; `_119.jpg` 90 KB → 1.18 MB). Conclusion: **Google Takeout downsampled the early-2000s originals** — master has better-quality versions for most files in that folder.

### 7. Size inversions — where master is *smaller* than takeout

Wrote `scratch_size_inversions.py` to find every same-named pair where master < takeout (threshold > 1 KB to ignore EXIF/metadata noise). Output: `size_inversions.tsv` (sorted by deficit, columns: deficit_bytes, relpath, before_bytes, after_bytes, ratio_after_over_before).

**659 inversions, 257.9 MB total deficit.**

Per-year:

| Year folder | Files | Deficit |
|---|---:|---:|
| 2000 - 2010 | 196 | 67.5 MB |
| 2014 | 160 | 27.6 MB |
| 2012 | 135 | 22.5 MB |
| 2026 | 2 | **75.3 MB** (the giant .mov) |
| 2018 | 43 | 4.3 MB |
| 2013 | 38 | 9.3 MB |
| 2025 | 29 | 7.9 MB |
| 2011 | 23 | 5.6 MB |
| 2020 | 12 | 4.6 MB |
| 2016 | 9 | 9.1 MB |
| 2017 | 8 | 1.0 MB |
| 2023 | 2 | 15.2 MB |
| 2022 | 1 | 5.9 MB |
| 2015 | 1 | 2.0 MB |
| **TOTAL** | **659** | **257.9 MB** |

**Dramatic cases (master holds a thumbnail-sized version):**
- `2026\2026-02-04 22.44.13_1.mov`: 108 MB → 30 MB (master 27% the size — looks like a video transcode)
- `2023\2023-10-14 20.50.00_1.mp4`: 17.8 MB → 1.9 MB (11%)
- `2012\2012-01-01 13.00.00_197/198/199.jpg`: 2.9 MB → 50–245 KB (1–8%, basically thumbnails)
- `2020\2020-01-01 13.00.00_13/15/16.jpeg`: 1.0–1.2 MB → 70–220 KB

**Batch-shrink pattern**: `2016\2016-03-11 18.25.00_9..14.jpg` — six files from the same shoot, all ~25–30% smaller in master with ratios 0.71–0.74. A single resize pass got applied to that whole batch in master.

**Bulk small inversions**: lots of `2000 - 2010\2000-01-01 13.00.00_*.jpg` entries where master is 50–80% the size of takeout. The early-2000s placeholder bucket has inversions going both directions — different photos were imported by different paths over the years.

---

## Where things stand RIGHT NOW
- `F:\PhotosCopy`: **15,098 files**, all structurally aligned with master conventions.
- All folders pass audit except `2011` flagged for the (now-resolved) collision; re-running the audit would show all-clear.
- EXIF tags match filenames everywhere (invariant holds).

### 8. Canonical-name normalization sweep

Discovered the takeout-ingest script (`update_filename_to_date_from_google_takeout_json_metadata.py:148-156`) uses a **per-extension** `_N` counter. That produces duplicate-looking names within a timestamp bucket — e.g., `2011-01-01 13.00.00_1.jpeg`, `_1.jpg`, `_1.mp4` all coexisted. PhotosCopy had 4476 `.jpeg` files (cleaner master only has 123) and many same-`_N`-across-extensions collisions.

**Promoted scratch into real tooling** (replaces the bug-fix done by hand):
- `photo_lib/canonical_renumber.py` — pure planner + two-phase applier
- `photo_lib/filename_pattern.py` — added `CANONICAL_FILENAME_PARTS_RE` with named groups
- `photo_lib/extensions.py` — added `CANONICAL_EXTENSION_ALIASES = {"jpeg": "jpg"}` and `canonical_extension()`
- `RenameFileToDateTool/normalize_canonical_names.py` — CLI script (`--path`, `--dry-run`)
- Tests: `tests/test_photo_lib/test_canonical_renumber.py` (13) + `tests/test_normalize_canonical_names.py` (4). Full suite: **203 passing** (was 186).

The tool does both:
- Canonicalizes extensions (`.jpeg`→`.jpg`, uppercase→lowercase)
- Renumbers each timestamp bucket to a contiguous `_1, _2, _3, ...` globally across extensions

Side-effects fall out for free:
- **Gap-closing**: delete `_3` from a series, next run pulls `_4` down to `_3`
- **Cross-extension uniqueness**: a bucket can no longer hold both `_1.jpg` and `_1.mp4`

**Applied to PhotosCopy**: 4771 renames in one pass (4337 pure `.jpeg`→`.jpg`, ~430 `_N` bumps from global-counter rule). Files now: 15098 (preserved), 8 extensions (down from 9). Note: this deliberately split Live Photo pairs that previously shared `_N` (.heic + .mp4 etc.). User chose Option A (fully global counter) over Option B (family-aware) after seeing the trade-off.

**Still TODO for the underlying bug**: fix `update_filename_to_date_from_google_takeout_json_metadata.py` so future ingests don't reintroduce the per-extension counter. Deferred until the fresh `F:\Photos` copy is ready for a re-import.

### 9. Duplicate-detection and library-combine tooling

Goal: combine master + PhotosCopy into one tree, detect duplicates with high confidence, suffix them `_a / _b / _c` for a one-pass manual review, then strip suffix off survivors. The user explicitly wants manual review — no script ever deletes photos.

**New code** (replaces the ad-hoc `scratch_dedup_2011.py`):
- `photo_lib/duplicate_finder.py` — three-tier grouping (file-bytes, pixel-bytes, perceptual-hash) for images + frame-pHash for videos via ffmpeg (5 frames per clip)
- `photo_lib/duplicate_cache.py` — sidecar SQLite at `<root>/.photo_hashes.db`; re-runs only hash changed files (size+mtime check)
- `photo_lib/duplicate_report.py` — self-contained HTML side-by-side report, base64-inlined thumbnails, tier badges
- `RenameFileToDateTool/find_duplicate_photos.py` — CLI: `scan`, `report`, `mark`, `finalize`
- `RenameFileToDateTool/combine_libraries.py` — copy multiple sources into one tree, bump `_N` on collision so nothing overwrites
- Added `imagehash>=4.3,<5` to `requirements.txt`

**Confidence tiers** (conservative, per user's choice):
- Tier 1: file SHA-256 match (literal duplicates)
- Tier 2: pixel SHA-256 match (images) OR identical 5-frame pHash tuple (videos)
- Tier 3: image pHash distance = 0 (no fuzzy match — pHash 1-5 isn't auto-marked)

**Quality ranking for `_a` winner**:
1. Higher pixel dimensions wins (or pixel-count-equivalent for videos via frame data later)
2. Larger file size
3. Lexicographic name (stable tie-break)

**Tests**: +36 (image hashing, video tier-1, cache round-trip, report HTML, scan/mark/finalize end-to-end, combine collision handling). Full suite: **239 passing** (was 203).

### 10. Audit-driven combine tests + dead code removal (commit `6145a1c`)

Audit of `combine_libraries.py` revealed the existing 6 tests verified algorithmic shape (filename sets) but not the data-safety invariant. Added 9 tests:
- Content preservation (byte-level): simple collision, three sources, cascading collisions, pre-existing dest content, cross-extension same-timestamp
- mtime preservation (the dedup cache depends on it for size+mtime invalidation)
- Edge cases: empty source, files at source root, mixed canonical/non-canonical in same folder

Also removed two dead helpers (`_resolve_dest_name`, `_next_free_canonical_name`) — defined but never called. The active path is `_resolve_dest_name_against_set`. Tests: 239 → 248.

### 11. Fuzzy pHash matching with low-entropy guard (commit `e434c0f`)

User pushed back on tier-3 strictness: with manual review in the loop, false-positives are cheap (an extra glance) but false-negatives leave duplicates hidden. Pre-fix the pHash tier required Hamming distance = 0, which would miss Google-Takeout re-encoded copies (typically differ by 4-12 bits).

Added `phash_hamming_threshold` parameter (default 8):
- 0 keeps the original exact-equality fast path
- > 0 enables fuzzy union-find pass with bit-wise Hamming distance, AFTER the exact pass has claimed its files
- Low-entropy pHashes (solid-color thumbnails ≈ all zeros, mostly all ones) are excluded from fuzzy matching to prevent giant fake clusters
- For videos: every frame in the 5-frame tuple must be within threshold

CLI: `--phash-threshold N` on the `report` and `mark` subcommands.

Tests: +15. 248 → 263 passing.

### 12. Takeout-ingest bug fixed (commit `df1c50b`)

Two changes in `plan_destination_filename`:
1. **Canonicalize the extension at ingest** via `canonical_extension()`. `.jpeg` → `.jpg`, uppercase → lowercase. Destination is canonical from day one.
2. **Global `_N` counter per base across all extensions**. Backed by a new shared `used_indices_by_base` dict, built once from the existing destination via `build_used_indices_by_base`, mutated as planning proceeds.

Dedup remains extension-aware where it should be: an incoming `.jpeg` is checked against existing `.jpg` files under the canonical-extension bucket, so a re-import after canonicalization is detected as a duplicate.

The orphan-Live-Photo-MP4 test was updated to reflect the new behavior: pre-fix `.heic + .mp4` both got `_1` (per-extension counter); post-fix `.heic` gets `_1` and `.mp4` gets `_2` (global counter). The user confirmed this is the desired behavior — Live Photo pairing is no longer needed.

Tests: +9. 263 → 272 passing.

### 13. USB photos pre-staged for combine

The user's `F:\initialPhotoStates\photosFromUSB` folder contains 1703 photos in a near-canonical format but flat (no year folders) and without `_N` suffixes. Examples:
- `2003-01-02 18.48.18.jpg` (1675 files — canonical timestamp, no `_N`)
- `2003-01-08 18.48.18-1.jpg` (28 files — canonical timestamp, hyphen-N instead of underscore-N)
- `482988_10151241204359080_890635342_n.jpg` (1 file — Facebook-export style, no parseable date; skipped, needs manual handling)

Used a one-shot Python script (in-conversation, not committed) to copy these into `F:\PhotosUSBStaged\<year>\<base>_<N>.<ext>` form. 1702 staged; the 1 Facebook-style file ignored. Sources untouched. `F:\PhotosUSBStaged` is the canonical-form 3rd source for combine.

### 14. Backup of all libraries to external drive

Before running the combine, the user copied to E: (external):
- `F:\initialPhotoStates` (zips, USB photos — the truly-original raw state)
- `F:\Photos` (post-takeout-ingest, untouched)
- `D:\Files\Pictures and Videos` (master library)

Now safe to run the combined-library workflow.

### 15. Pilot run on year 2014 — caught a data-loss bug

Started a single-year pilot (2014: 852 master + 782 PhotosCopy = 1634 files) before committing to the full library run. The pilot chains:
1. `combine_libraries` master/2014 + PhotosCopy/2014 → `F:\PhotosCombined_pilot`
2. `normalize_canonical_names` on the pilot dest
3. `find_duplicate_photos.py scan`
4. `find_duplicate_photos.py report --phash-threshold 8`

**The pilot caught a silent data-loss bug** in `combine_libraries.plan_combine` (commit `26b7ae8`): master has uppercase extensions (`.JPG`, `.MOV`); PhotosCopy has lowercase (`.jpg`, `.mov`). Python's case-sensitive string compare in the collision-detection slot_set treated `_1.JPG` and `_1.jpg` as distinct dest paths — both got planned. But NTFS is case-insensitive: when the second copy hit disk, it silently overwrote the first. In 2014 alone, 286 master files would have been lost without warning. Master itself was untouched (we copy from sources, not into them), but the pilot dest ended up with 1349 files instead of 1634.

**Fix**: lowercase the slot_set entries and the membership tests in `_resolve_dest_name_against_set`. Original-case filenames preserved on disk; only the COMPARISON is case-folded. Added 4 regression tests:
- `.JPG` + `.jpg` in two sources detected as a collision → second bumps to `_2.jpg`
- Both files exist in dest with their original bytes intact after apply
- Pre-existing uppercase dest content blocks an incoming lowercase source
- Four-way case variation (`.JPG/.Jpg/.jPg/.jpg`) all collide pairwise

Pilot v2 ran clean: 1634 files preserved end-to-end (matches sources exactly). 650 case-insensitive collisions were detected and renamed during combine. 784 duplicate groups at `--phash-threshold 8`.

### 16. Mark-step improvements driven by pilot feedback (commit `abe5fe3`)

Three changes after looking at the pilot's `mark` dry-run output:

**(a) Adjacent-sort marking.** Pre-fix `mark` kept each file's own `_N` and just appended a letter. A group at `_1, _14, _29` produced `_1_a, _14_b, _29_c` — which sort scattered across the folder, defeating the whole point of marking. Now the winner's `_N` becomes the SHARED group prefix: `_1_a, _1_b, _1_c`. Each file keeps its own extension (so `.heic + .mp4 + .mov` pairs at one timestamp stay distinguishable within a group).

**(b) Cache-preserving renames.** `mark`/`finalize` previously called `cache.forget(old_path)` after each rename, invalidating the expensive hash data. Re-running `report` after `mark` would have shown an empty report (old paths gone from cache, new paths never added). Added `cache.rename(old, new)` to `photo_lib/duplicate_cache.py`. `mark` and `finalize` now use it — the hash data is preserved under the new path, so a subsequent `report` reads the cache directly and writes a refreshed HTML with the post-`mark` filenames. **No re-scan needed.** Saves an hour on the full library.

**(c) Singletons-by-year report.** `report` now also writes `singletons_report.html` alongside `duplicate_report.html`. Lists files that didn't cluster with anything at the chosen pHash threshold, grouped by year folder. Useful for spotting photos that exist in only one source library, or for catching cases the dedup missed. On the 2014 pilot: 57 singletons out of 1634 files; 0.8 MB report.

Tests: +7 in this commit (cache rename, plan_mark winner-idx, mixed-extension group preservation, three-file adjacency, singletons grouping).

### 17. Finalize extended to handle multi-survivor groups

The pre-existing `finalize` only handled the lone-survivor case: a `_a`/`_b`/`_c` group where the user kept exactly one file got the `_<letter>` suffix stripped back to canonical form. Multi-survivor case (user keeps 2+ from a group because they look visually distinct on review) was a no-op — the files would be stuck with non-canonical names indefinitely.

Extended: when 2+ letters survive a group, the first survivor (alphabetically — usually `_a`) takes the group's original idx, and subsequent survivors get the next free idx in that timestamp bucket. The algorithm processes ALL `_a` survivors across the folder before any `_b` survivors, so an earlier group's `_b` overflow can't grab the idx a later group's `_a` was about to claim.

Edge cases covered by tests:
- Lone `_a` survivor → stripped to canonical (unchanged)
- Lone `_b` survivor (user deleted `_a`) → also stripped to canonical
- `_a + _b` both kept → `_a → _N`, `_b → _N+1`
- `_a + _b` both kept, but `_N+1` already occupied by an unrelated canonical file → `_b` bumps to next free
- Two adjacent groups both with `_a + _b` → all four `_a`s claim their preferred slots first, then `_b`s overflow into next free
- `_a` survivor whose canonical `_N` is now occupied by something else → bumps to next free instead of skipping

286 tests passing now (was 283 → +3 net for the multi-survivor cases).

---

## 2026-05-21 — Cross-date duplicate preservation (commit `9d07344`)

### 18. Both dates preserved when duplicates have different timestamps

Discovered while reviewing the workflow: the pre-existing `plan_mark` collapsed a duplicate group's losers under the WINNER's `<base>_<idx>` prefix and left each file in its original folder. For a same-timestamp group that worked. For a cross-date group (winner from 2014, duplicate from 2015), the loser's filename inherited the 2014 base — losing its 2015 timestamp — but the file was still physically sitting in the `2015\` folder, mismatched with its new name. Neither "adjacent on sort" nor "both dates visible for review" was actually achieved.

**Spec'd before implementing**:
- Filename marker: `<winner_base>_<winner_idx>_<letter>__from_<loser_base>.<ext>` (double-underscore + `from_` — unambiguous to regex, no special chars).
- Sensitivity: ANY base difference triggers cross-date treatment (same-day burst-time differences also get the marker — maximum information preservation).
- HTML report includes a `cross-date` badge per group.

**Changes**:
- `photo_lib/duplicate_finder.py` —
  - `MARKED_FILENAME_RE` extended with optional `__from_<origin_base>` capture group; one regex handles both forms.
  - `plan_mark` moves EVERY loser into the winner's folder (so they sort adjacent in Explorer). Cross-date losers carry the `__from_<loser_base>` marker; same-base losers keep the existing `_a/_b/_c` form unchanged.
  - `plan_finalize` rewritten as a two-phase walk: phase 1 collects all marked entries + per-folder canonical idx buckets across the whole tree, phase 2 does the two-pass-by-letter allocation. Surviving cross-date losers (`_b`/`_c` with the `__from_` marker) are sent back to their origin year folder and re-canonicalized to the lowest free idx in that bucket. Bundled-early years (2000-2010) route to the `2000 - 2010` folder via a new `_target_folder_for_base` helper that reuses the `BUNDLED_EARLY_*` constants from `photo_lib/config.py`.
  - `apply_simple_rename_plan` now `os.makedirs(..., exist_ok=True)` on the target dir before the final rename, so a cross-folder destination that doesn't exist yet is created on demand.
- `photo_lib/duplicate_report.py` — new `_is_cross_date_group` helper (checks if canonical members have ≥2 distinct `<base>`); `_render_group` emits `<span class="cross-date-badge">cross-date</span>` when applicable.

**Behavior example**:
```
Before mark:
  F:\PhotosCombined\2014\2014-06-15 10.00.00_1.jpg          (winner)
  F:\PhotosCombined\2015\2015-08-20 14.30.00_3.jpg          (loser)

After mark:
  F:\PhotosCombined\2014\2014-06-15 10.00.00_1_a.jpg
  F:\PhotosCombined\2014\2014-06-15 10.00.00_1_b__from_2015-08-20 14.30.00.jpg
  (both adjacent in Explorer; both dates visible)

After finalize (user kept BOTH — they weren't duplicates after all):
  F:\PhotosCombined\2014\2014-06-15 10.00.00_1.jpg          (_a survivor)
  F:\PhotosCombined\2015\2015-08-20 14.30.00_<free>.jpg     (_b sent home)
```

Tests: +14. 286 → 300 passing. Coverage includes: cross-date mark across two and three distinct dates, same-base regression, finalize returning loser to origin year, idx-bump-when-taken at destination, two losers sharing destination bucket, bundled-early routing, multi-survivor mixed local+remote, missing origin folder created on apply, cross-date badge present/absent on report, non-canonical paths don't trigger badge.

**The earlier session-notes workflow step 6 ("Manual review: open each year folder, name-sort, delete unwanted") still works** — but the user can now compare cross-date pairs side-by-side in ONE folder rather than tabbing between year folders to figure out which date is the true one.

---

## Workflow once pilot is validated
1. **Combine all three sources**:
   ```powershell
   python combine_libraries.py `
       --source "D:\Files\Pictures and Videos" `
       --source "F:\PhotosCopy" `
       --source "F:\PhotosUSBStaged" `
       --dest "F:\PhotosCombined"
   ```
2. `normalize_canonical_names.py --path F:\PhotosCombined` (cleans up any collision-bumped names from combine + mixed-case/jpeg from master)
3. `find_duplicate_photos.py scan --path F:\PhotosCombined` (one-time, ~1-3 hr)
4. `find_duplicate_photos.py report --path F:\PhotosCombined --phash-threshold 8` → open `duplicate_report.html` in browser to gauge scope
5. `find_duplicate_photos.py mark --path F:\PhotosCombined --phash-threshold 8` (renames to `_a/_b/_c`)
6. **Manual review**: open each year folder in Explorer, name-sort, delete unwanted `_b/_c/...` files
7. `find_duplicate_photos.py finalize --path F:\PhotosCombined` (strips `_a` suffix from lone survivors)

---

## Open follow-ups (not blockers)
1. **Size inversions** — `size_inversions.tsv` (regenerable from `scratch_size_inversions.py` if needed; not committed) has 659 cases ranked by deficit. Top ~30 are the most actionable. Will likely be resolved by the dedup workflow.
2. **185 isolated_missing files** — generate the explicit list, spot-check whether they're real losses or false negatives in the pairing logic.
3. **2026 −510 gap** — most are same-date sibling drift; only 8 are truly isolated. Could investigate why timestamps differ so widely for a year still being photographed.
4. **3 `.heic` files containing `.heif` payload** in `2026` — intentional per yesterday's convention, but document/encode this in `audit_master.py` so it stops flagging them.
5. **Manual fixes from PhotosCopy still ad-hoc** — folder rename `2000-2010` → `2000 - 2010`, extension-mismatch renames (170 files), wrong-year-folder moves (10 files), extensionless MOV → `.mov` (1 file). All would need to be redone by hand if rebuilding PhotosCopy from `F:\Photos`. Could be codified as small fixers if needed.
6. **One Facebook-style USB file** — `482988_10151241204359080_890635342_n.jpg` skipped during USB staging; has no parseable date in the filename. Manual handling required if it should join the library.
